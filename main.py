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

# ---- 数据库连接 & 建表 ----
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id          BIGINT    NOT NULL,
    user_id          BIGINT    NOT NULL,
    currency         TEXT      DEFAULT 'RMB',
    rate             DOUBLE PRECISION DEFAULT 0,
    fee_rate         DOUBLE PRECISION DEFAULT 0,
    commission_rate  DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id               SERIAL PRIMARY KEY,
    chat_id          BIGINT    NOT NULL,
    user_id          BIGINT    NOT NULL,
    name             TEXT,
    amount           DOUBLE PRECISION,
    rate             DOUBLE PRECISION,
    fee_rate         DOUBLE PRECISION,
    commission_rate  DOUBLE PRECISION,
    currency         TEXT,
    date             TIMESTAMP,
    message_id       BIGINT
);
""")
conn.commit()

# ---- 工具函数 ----
def ceil2(x):
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    else:
        return 'RMB', 0, 0, 0

def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date",
        (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total = sum(r['amount'] for r in rows)
    currency, rate, fee, commission = get_settings(chat_id, user_id)

    converted = ceil2(total * (1 - fee/100) / rate) if rate else 0
    comm_rmb = ceil2(total * commission/100)
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    # 明细
    lines = []
    for idx, r in enumerate(rows, start=1):
        no = f"{idx:03d}"
        ts = r['date'].strftime('%d-%m-%Y %H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        lines.append(f"{no}. {ts} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r['commission_rate'] > 0:
            c = ceil2(r['amount'] * r['commission_rate']/100)
            lines.append(f"{no}. {ts} {r['amount']}*{r['commission_rate']/100:.2f} = {c} 【佣金】")

    body = "\n".join(lines)
    body += f"\n\n已入款（{len(rows)}笔）：{total} ({currency})"
    body += f"\n已下发（0笔）：0.0 (USDT)\n\n"
    body += f"总入款金额：{total} ({currency})"
    body += f"\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    body += f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted} (USDT)"
    body += f"\n已下发：0.0({currency}) | 0.0 (USDT)"
    body += f"\n未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted} (USDT)"
    if commission > 0:
        body += f"\n\n中介佣金应下发：{comm_rmb}({currency}) | {comm_usdt} (USDT)"
    return body

# ---- Bot Handlers ----
@bot.message_handler(commands=['start'])
def on_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '/trade')
    kb.row('📊 汇总', '/summary', '🛠 重置', '/reset')
    bot.send_message(m.chat.id, "欢迎使用 LX 记账机器人 ✅", reply_markup=kb)

@bot.message_handler(commands=['id'])
def on_id(m):
    bot.reply_to(m, f"chat_id = {m.chat.id}\nuser_id = {m.from_user.id}")

@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易','/trade'])
def on_trade(m):
    bot.reply_to(m,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith('设置交易指令'))
def on_set_config(m):
    text = m.text.replace('：',':')
    currency = None; rate = None; fee = None; com = None; errors = []
    for line in text.splitlines():
        if line.startswith('设置货币'):
            _,v = line.split(':',1); currency = v.strip()
        if line.startswith('设置汇率'):
            _,v = line.split(':',1)
            try: rate = float(v); 
            except: errors.append("汇率格式错误")
        if line.startswith('设置费率'):
            _,v = line.split(':',1)
            try: fee = float(v);
            except: errors.append("费率格式错误")
        if line.startswith('中介佣金'):
            _,v = line.split(':',1)
            try: com = float(v);
            except: errors.append("中介佣金格式错误")

    if rate is None:
        return bot.reply_to(m, "设置错误，至少需要提供汇率")
    if errors:
        return bot.reply_to(m, "设置错误\n" + "\n".join(errors))

    try:
        cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
        """, (m.chat.id, m.from_user.id,
              currency or 'RMB', rate, fee or 0, com or 0))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(m, f"设置失败：{e}")

    return bot.reply_to(m,
        "✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}%\n"
        f"中介佣金：{com}%"
    )

# 收入/支出记录，支持 +1000、-500、加1000、减500
@bot.message_handler(func=lambda m: re.match(r'^([+\-加减])\s*\d+(\.\d+)?', m.text or ''))
def handle_amount(m):
    # 调试：先确认这个 handler 有没有被执行
    bot.reply_to(m, "【DEBUG】收到了入笔：" + m.text)

    sign = m.text.strip()[0]
    num = re.search(r'\d+(\.\d+)?', m.text).group()
    amt = float(num) * ( -1 if sign in '-减' else 1 )
    name = m.from_user.username or m.from_user.first_name or '匿名'
    cur, rate_, fee_, com_ = get_settings(m.chat.id, m.from_user.id)
    now = datetime.now()

    try:
        cursor.execute("""
        INSERT INTO transactions(
          chat_id, user_id, name, amount,
          rate, fee_rate, commission_rate,
          currency, date, message_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (m.chat.id, m.from_user.id, name, amt,
              rate_, fee_, com_, cur, now, m.message_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(m, "记录失败：" + str(e))

    return bot.reply_to(m,
        f"✅ 已入款 {amt} ({cur})\n" +
        show_summary(m.chat.id, m.from_user.id)
    )

@bot.message_handler(commands=['summary'])
def on_summary(m):
    bot.reply_to(m, show_summary(m.chat.id, m.from_user.id))

@bot.message_handler(commands=['reset'])
def on_reset(m):
    cursor.execute(
        "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
        (m.chat.id, m.from_user.id)
    )
    conn.commit()
    bot.reply_to(m, "✅ 已清空记录")

# 不要其他 polling 或 webhook，只用下面一行
bot.infinity_polling()
