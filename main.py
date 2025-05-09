import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import math
import re
import os

TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(TOKEN)

# 连接数据库
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# --- 自动修正表结构 start ---

# 1) transactions 如果缺少 user_id、name、message_id 字段就加上
cursor.execute("""
ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS user_id BIGINT,
  ADD COLUMN IF NOT EXISTS name TEXT,
  ADD COLUMN IF NOT EXISTS message_id BIGINT
""")

# 2) settings 表上补主键 (chat_id, user_id)
#    先尝试删掉旧的默认主键，再加上复合主键
cursor.execute("""
ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_pkey;
ALTER TABLE settings
  ADD CONSTRAINT settings_pkey PRIMARY KEY (chat_id, user_id)
""")
conn.commit()
# --- 自动修正表结构 end ---

# 如果表不存在，就创建（CREATE TABLE IF NOT EXISTS）
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    user_id BIGINT,
    name TEXT,
    amount DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    fee_rate DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date TEXT,
    message_id BIGINT
)
''')
conn.commit()

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute('''
        SELECT currency, rate, fee_rate, commission_rate
          FROM settings
         WHERE chat_id=%s AND user_id=%s
    ''', (chat_id, user_id))
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

def show_summary(chat_id, user_id):
    cursor.execute('''
        SELECT * FROM transactions
         WHERE chat_id=%s AND user_id=%s
    ''', (chat_id, user_id))
    records = cursor.fetchall()
    total = sum(r['amount'] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    converted = ceil2(total * (1 - fee/100) / rate) if rate else 0
    comm_rmb = ceil2(total * commission/100)
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    lines = []
    for idx, row in enumerate(records, 1):
        t = datetime.strptime(row['date'], '%Y-%m-%d %H:%M:%S').strftime('%H:%M:%S')
        after_fee = row['amount'] * (1 - row['fee_rate']/100)
        usdt = ceil2(after_fee / row['rate']) if row['rate'] else 0
        line = f"{idx}. {t}  {row['amount']}*{1-row['fee_rate']/100:.2f}/{row['rate']} = {usdt}  {row['name']}"
        lines.append(line)
        if row['commission_rate']>0:
            cm = ceil2(row['amount']*row['commission_rate']/100)
            lines.append(f"{idx}. {t}  {row['amount']}*{row['commission_rate']/100:.2f} = {cm} 【佣金】")
    summary = "\n".join(lines)

    footer = (
        f"\n已入款（{len(records)}笔）：{total} ({currency})\n"
        f"已下发（0笔）：0.0 (USDT)\n\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted} (USDT)\n"
    )
    if commission>0:
        footer += f"\n中介佣金应下发：{comm_rmb}({currency}) | {comm_usdt} (USDT)"

    return summary + footer

# --- Bot 命令处理 ---
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('🔁 计算重启', '📊 汇总')
    kb.row('❓ 需要帮助', '🛠️ 定制机器人')
    bot.send_message(msg.chat.id, "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：", reply_markup=kb)

@bot.message_handler(commands=['id'])
def cmd_id(msg):
    bot.reply_to(msg, f"chat_id: {msg.chat.id}\nuser_id: {msg.from_user.id}")

@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def cmd_show_trade(m):
    txt = (
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(m, txt)

@bot.message_handler(func=lambda m: m.text and '设置交易指令' in m.text)
def set_trade_config(m):
    cid, uid = m.chat.id, m.from_user.id
    text = m.text.replace('：',':').upper()
    cur = rate = fee = comm = None
    errs = []
    for L in text.splitlines():
        L = L.strip().replace(' ','')
        if L.startswith('设置货币'):
            v = L.split(':',1)[1]; cur = re.sub(r'[^A-Z]','',v)
        if L.startswith('设置汇率'):
            try: rate = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('汇率格式错误')
        if L.startswith('设置费率'):
            try: fee = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('费率格式错误')
        if L.startswith('中介佣金'):
            try: comm = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('中介佣金请设置数字')
    if errs:
        bot.reply_to(m, "设置错误\n"+"\n".join(errs))
        return
    if rate is None:
        bot.reply_to(m, "设置错误，缺少汇率")
        return

    try:
        cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
        """, (cid, uid, cur or 'RMB', rate, fee or 0, comm or 0))
        conn.commit()
        bot.reply_to(m,
            f"✅ 设置成功\n"
            f"设置货币：{cur or 'RMB'}\n"
            f"设置汇率：{rate}\n"
            f"设置费率：{fee or 0}%\n"
            f"中介佣金：{comm or 0}%"
        )
    except Exception as e:
        conn.rollback()
        bot.reply_to(m, f"设置失败：{e}")

@bot.message_handler(func=lambda m: re.match(r'^([+加]\s*\d+)|(.+\s*[+加]\s*\d+)', m.text))
def handle_amount(m):
    cid, uid = m.chat.id, m.from_user.id
    txt = m.text.strip()
    # +1000 或 名称+1000
    if txt.startswith(('+','加')):
        amt = float(re.findall(r'\d+\.?\d*',txt)[0])
        name = m.from_user.first_name or '匿名'
    else:
        name, n = re.findall(r'(.+)[+加](\d+\.?\d*)', txt)[0]
        amt = float(n); name = name.strip()

    cur, rate, fee, comm = get_settings(cid, uid)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
      INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (cid, uid, name, amt, rate, fee, comm, cur, now, m.message_id))
    conn.commit()

    bot.reply_to(m,
        f"✅ 已入款 +{amt} ({cur})\n"
        f"编号：{m.message_id}\n"
        + show_summary(cid, uid)
    )

# 去掉 webhook，直接轮询
bot.remove_webhook()
bot.infinity_polling()
