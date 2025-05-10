import os
import re
import math
from datetime import datetime
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor

# 从环境变量读取
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(TOKEN)

# 建立数据库连接
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 初始化表结构
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT      DEFAULT 'RMB',
    rate DOUBLE PRECISION       DEFAULT 0,
    fee_rate DOUBLE PRECISION   DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
""")
cursor.execute("""
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
    date TIMESTAMP,
    message_id BIGINT
);
""")
conn.commit()

# 帮助函数：向上取两位小数
def ceil2(x):
    return math.ceil(x * 100) / 100.0

# 读取当前配置
def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings "
        "WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        return ('RMB', 0, 0, 0)
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate'])

# 汇总并格式化消息
def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    recs = cursor.fetchall()
    total = sum(r['amount'] for r in recs)
    currency, rate, fee, comm = get_settings(chat_id, user_id)
    converted_total = ceil2(total * (1 - fee/100) / rate) if rate else 0
    comm_rmb = ceil2(total * (comm/100))
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(recs, 1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        lines.append(f"{idx}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r['commission_rate']>0:
            c_amt = ceil2(r['amount'] * r['commission_rate']/100)
            lines.append(f"{idx}. {t} {r['amount']}*{r['commission_rate']/100:.2f} = {c_amt} 【佣金】")
    body = "\n".join(lines)

    footer = (
        f"\n已入款（{len(recs)}笔）：{total} ({currency})\n"
        f"已下发（0笔）：0 (USDT)\n\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee:.1f}%\n佣金：{comm:.1f}%\n\n"
        f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total}(USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total}(USDT)\n"
    )
    if comm>0:
        footer += f"\n中介佣金应下发：{comm_rmb}({currency}) | {comm_usdt} (USDT)"
    return body + footer

# /start
@bot.message_handler(commands=['start'])
def cmd_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('🔁 重启计算', '📊 汇总')
    kb.row('❓ 帮助', '🛠️ 定制')
    bot.send_message(m.chat.id,
                     "欢迎使用 LX 记账机器人 ✅\n请选择：",
                     reply_markup=kb)

# /id
@bot.message_handler(commands=['id'])
def cmd_id(m):
    bot.reply_to(m, f"chat_id={m.chat.id}\nuser_id={m.from_user.id}")

# 显示模板
@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def cmd_show(m):
    tpl = (
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(m, tpl)

# 保存配置
@bot.message_handler(func=lambda m: '设置交易指令' in m.text)
def set_trade_config(m):
    text = m.text.replace('：',':')
    currency = rate = fee = comm = None
    errs = []
    for L in text.splitlines():
        L2 = L.strip().replace(' ','')
        if L2.startswith('设置货币'):
            v = L2.split(':',1)[1]
            currency = re.sub(r'[^A-Za-z]','',v).upper()
        if L2.startswith('设置汇率'):
            try: rate = float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append('汇率格式错误')
        if L2.startswith('设置费率'):
            try: fee = float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append('费率格式错误')
        if L2.startswith('中介佣金'):
            try: comm = float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append('中介佣金格式错误')
    if errs or rate is None:
        bot.reply_to(m, "设置错误\n" + "\n".join(errs or ['缺少汇率']))
        return

    cid, uid = m.chat.id, m.from_user.id
    cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE
          SET currency=EXCLUDED.currency,
              rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate,
              commission_rate=EXCLUDED.commission_rate
    """, (cid,uid,currency,rate,fee or 0,comm or 0))
    conn.commit()
    bot.reply_to(m, (
        "✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate:.1f}\n"
        f"设置费率：{fee or 0:.1f}%\n"
        f"中介佣金：{comm or 0:.1f}%"
    ))

# 入笔处理：+1000 或 名称+1000
@bot.message_handler(func=lambda m: re.match(r'^[\+\-加]\s*\d+(\.\d*)?$',m.text.strip()) 
                              or re.search(r'\D+[+\-加]\s*\d+(\.\d*)?',m.text))
def handle_amount(m):
    cid, uid = m.chat.id, m.from_user.id
    txt = m.text.strip()
    bot.send_message(cid, f"[DEBUG] 收到了入笔：{txt}")
    # 提取数量和姓名
    m1 = re.match(r'^[+\-加]\s*(\d+\.?\d*)$', txt)
    if m1:
        amt = float(m1.group(1))
        name = m.from_user.username or m.from_user.first_name or '匿名'
    else:
        parts = re.split(r'[+\-加]', txt, maxsplit=1)
        name = parts[0].strip() or (m.from_user.username or '匿名')
        amt  = float(re.findall(r'\d+\.?\d*', parts[1])[0])
    # 读取配置
    cur, rate, fee, comm = get_settings(cid, uid)
    now = datetime.now()
    cursor.execute("""
        INSERT INTO transactions(
          chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, date, message_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (cid,uid,name,amt,rate,fee,comm,cur,now,m.message_id))
    conn.commit()

    # 输出详情
    summary = show_summary(cid, uid)
    bot.reply_to(m, f"✅ 已入款 {amt} ({cur})\n编号：{m.message_id}\n\n" + summary)

# 启动
bot.remove_webhook()
bot.infinity_polling()
