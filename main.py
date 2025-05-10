import os
import re
import math
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

# 从环境变量读取
TOKEN         = os.getenv('TOKEN')
DATABASE_URL  = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(TOKEN)

# 连接数据库
conn   = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 第一次启动时，确保表结构正确 —— 
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id         BIGINT,
  user_id         BIGINT,
  currency        TEXT    DEFAULT 'RMB',
  rate            DOUBLE PRECISION DEFAULT 0,
  fee_rate        DOUBLE PRECISION DEFAULT 0,
  commission_rate DOUBLE PRECISION DEFAULT 0,
  PRIMARY KEY (chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
  id               SERIAL      PRIMARY KEY,
  chat_id          BIGINT,
  user_id          BIGINT,
  name             TEXT,
  amount           DOUBLE PRECISION,
  rate             DOUBLE PRECISION,
  fee_rate         DOUBLE PRECISION,
  commission_rate  DOUBLE PRECISION,
  currency         TEXT,
  date             TIMESTAMP   DEFAULT NOW()
);
""")
conn.commit()

# —— 工具函数 —— 
def ceil2(x):
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
      "SELECT currency,rate,fee_rate,commission_rate FROM settings "
      "WHERE chat_id=%s AND user_id=%s",
      (chat_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        return ('RMB', 0, 0, 0)
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate'])

def show_summary(chat_id, user_id):
    cursor.execute(
      "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
      (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total = sum(r['amount'] for r in rows)
    currency, rate, fee, comm = get_settings(chat_id, user_id)
    out = []
    for i,r in enumerate(rows, start=1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        line = f"{i}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}"
        out.append(line)
        if r['commission_rate']>0:
            c_amt = ceil2(r['amount'] * r['commission_rate']/100)
            out.append(f"{i}. {t} {r['amount']}*{r['commission_rate']/100:.2f} = {c_amt} 【佣金】")
    summary = "\n".join(out)
    converted = ceil2(total*(1-fee/100)/rate) if rate else 0
    comm_rmb   = ceil2(total*comm/100)
    comm_usdt  = ceil2(comm_rmb/rate) if rate else 0

    summary += (
      f"\n\n已入款（{len(rows)}笔）：{total} ({currency})\n"
      f"总入款金额：{total} ({currency})\n"
      f"汇率：{rate}\n费率：{fee}%\n佣金：{comm}%\n\n"
      f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted}(USDT)\n"
      f"已下发：0.0({currency}) | 0.0 (USDT)\n"
      f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted}(USDT)\n"
    )
    if comm>0:
        summary += f"\n中介佣金应下发：{comm_rmb}({currency}) | {comm_usdt}(USDT)"
    return summary

# —— /start ——
@bot.message_handler(commands=['start'])
def cmd_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '/trade')
    kb.row('📊 汇总', '/reset')
    bot.send_message(m.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=kb
    )

# —— /trade —— 显示格式
@bot.message_handler(commands=['trade'])
def cmd_trade(m):
    bot.reply_to(m,
      "设置交易指令\n"
      "设置货币：RMB\n"
      "设置汇率：0\n"
      "设置费率：0\n"
      "中介佣金：0"
    )

# —— 真正处理“设置交易指令” ——
@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def handle_set(m):
    chat, user = m.chat.id, m.from_user.id
    txt = m.text.replace('：',':')
    c=r=f=cm=None; errs=[]
    for L in txt.split('\n'):
        L=L.replace(' ','')
        if '货币' in L:
            m1=re.search(r'货币[:：](.+)',L); c=(m1.group(1).upper() if m1 else None)
        if '汇率' in L:
            try: r=float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append("汇率格式错")
        if '费率' in L:
            try: f=float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append("费率格式错")
        if '佣金' in L:
            try: cm=float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append("佣金格式错")
    if errs:
        return bot.reply_to(m, "设置错误\n" + "\n".join(errs))
    if r is None:
        return bot.reply_to(m, "设置错误，缺少汇率")
    cursor.execute("""
      INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
      VALUES(%s,%s,%s,%s,%s,%s)
      ON CONFLICT(chat_id,user_id) DO UPDATE
        SET currency=EXCLUDED.currency,
            rate=EXCLUDED.rate,
            fee_rate=EXCLUDED.fee_rate,
            commission_rate=EXCLUDED.commission_rate
    """,(chat,user,c or 'RMB',r,f or 0,cm or 0))
    conn.commit()
    bot.reply_to(m,
      "✅ 设置成功\n"
      f"货币：{c or 'RMB'}\n汇率：{r}\n费率：{f or 0}%\n佣金：{cm or 0}%"
    )

# —— /reset 清空本用户所有记录 ——
@bot.message_handler(commands=['reset'])
def cmd_reset(m):
    cursor.execute(
      "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
      (m.chat.id, m.from_user.id)
    )
    conn.commit()
    bot.reply_to(m, "✅ 已清空记录")

# —— 处理入笔：+1000 或 名称+1000 ——
@bot.message_handler(func=lambda m: re.match(r'^([+加]\d+)|(.+[+加]\d+)', m.text or ''))
def handle_amount(m):
    chat, user = m.chat.id, m.from_user.id
    cur, rate, fee, cm = get_settings(chat, user)
    if rate==0:
        return bot.reply_to(m, "请先设置交易并填写汇率，才能入笔")

    txt = m.text.strip()
    # 匹配 +1000 或 名称+1000
    if txt.startswith('+') or txt.startswith('加'):
        name = m.from_user.first_name or '匿名'
        amt  = float(re.findall(r'\d+\.?\d*', txt)[0])
    else:
        parts = re.findall(r'(.+?)[+加](\d+\.?\d*)', txt)
        name = parts[0][0].strip()
        amt  = float(parts[0][1])

    cursor.execute("""
      INSERT INTO transactions(
        chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency
      ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
    """,(chat,user,name,amt,rate,fee,cm,cur))
    conn.commit()

    bot.reply_to(m,
      f"✅ 已入款 {amt} ({cur})\n\n"
      + show_summary(chat,user)
    )

# —— 启动轮询 —— 
bot.remove_webhook()
bot.infinity_polling()
