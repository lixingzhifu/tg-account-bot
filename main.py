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

# 连接数据库
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# ─── 1. 建表 ───────────────────────────────────────────────────────────────
# 如果你已经手动 DROP 过旧表，这里会自动重建；否则会检查不存在再创建
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id            BIGINT     NOT NULL,
    user_id            BIGINT     NOT NULL,
    currency           TEXT       DEFAULT 'RMB',
    rate               DOUBLE PRECISION DEFAULT 0,
    fee_rate           DOUBLE PRECISION DEFAULT 0,
    commission_rate    DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id                 SERIAL     PRIMARY KEY,
    chat_id            BIGINT     NOT NULL,
    user_id            BIGINT     NOT NULL,
    name               TEXT,
    amount             DOUBLE PRECISION,
    rate               DOUBLE PRECISION,
    fee_rate           DOUBLE PRECISION,
    commission_rate    DOUBLE PRECISION,
    currency           TEXT,
    date               TIMESTAMP,
    message_id         BIGINT
);
""")
conn.commit()

# ─── 2. 辅助函数 ────────────────────────────────────────────────────────────
def ceil2(x):
    """ 保留两位小数（向上取整） """
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    """ 从 settings 表拿本群本用户的配置 """
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings "
        "WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    # 如果没记录，返回默认
    return 'RMB', 0, 0, 0

def show_summary(chat_id, user_id):
    """ 拼接当日所有入款明细 + 汇总 """
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    records = cursor.fetchall()
    total = sum(r['amount'] for r in records)

    currency, rate, fee_rate, com_rate = get_settings(chat_id, user_id)
    after_fee_total = ceil2(total * (1 - fee_rate / 100))
    usdt_total = ceil2(after_fee_total / rate) if rate else 0
    com_total_rmb = ceil2(total * com_rate / 100)
    com_total_usdt = ceil2(com_total_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(records, start=1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate'] / 100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        line = f"{idx}. {t} {r['amount']}*{1 - r['fee_rate'] / 100:.2f}/{r['rate']} = {usdt}  {r['name']}"
        if r['commission_rate'] > 0:
            com_amt = ceil2(r['amount'] * r['commission_rate'] / 100)
            line += f"\n{idx}. {t} {r['amount']}*{r['commission_rate'] / 100:.2f} = {com_amt} 【佣金】"
        lines.append(line)

    body = "\n".join(lines)
    footer = (
        f"\n已入款（{len(records)}笔）：{total} ({currency})\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee_rate}%\n佣金：{com_rate}%\n\n"
        f"应下发：{after_fee_total}({currency}) | {usdt_total} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{after_fee_total}({currency}) | {usdt_total} (USDT)\n"
    )
    if com_rate > 0:
        footer += f"\n中介佣金应下发：{com_total_rmb}({currency}) | {com_total_usdt} (USDT)"
    return body + footer

# ─── 3. 命令处理 ────────────────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('💱 设置交易', '📘 指令大全')
    markup.row('🔁 重置记录', '📊 汇总')
    markup.row('❓ 帮助', '🛠️ 定制')
    bot.send_message(
        msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=markup
    )

@bot.message_handler(commands=['id'])
def cmd_id(msg):
    bot.reply_to(
        msg,
        f"chat_id = {msg.chat.id}\nuser_id = {msg.from_user.id}"
    )

@bot.message_handler(func=lambda m: m.text in ['设置交易', '💱 设置交易'])
def ask_setting(msg):
    bot.reply_to(msg,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

@bot.message_handler(func=lambda m: '设置交易指令' in m.text)
def set_trade(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    text = msg.text.replace('：', ':').strip()
    cur = None
    rate = fee = com = None
    errs = []

    for line in text.split('\n'):
        if '货币:' in line:
            cur = line.split(':',1)[1].strip()
        if '汇率:' in line:
            try:
                rate = float(line.split(':',1)[1])
            except:
                errs.append('汇率格式错误')
        if '费率:' in line:
            try:
                fee = float(line.split(':',1)[1])
            except:
                errs.append('费率格式错误')
        if '中介佣金:' in line:
            try:
                com = float(line.split(':',1)[1])
            except:
                errs.append('中介佣金格式错误')

    if rate is None:
        errs.append('缺少汇率')
    if errs:
        bot.reply_to(msg, '设置错误\n' + '\n'.join(errs))
        return

    cur = cur or 'RMB'
    fee = fee or 0
    com = com or 0

    try:
        cursor.execute("""
            INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                currency = EXCLUDED.currency,
                rate = EXCLUDED.rate,
                fee_rate = EXCLUDED.fee_rate,
                commission_rate = EXCLUDED.commission_rate
        """, (chat_id, user_id, cur, rate, fee, com))
        conn.commit()
        bot.reply_to(msg,
            f"✅ 设置成功\n"
            f"设置货币：{cur}\n"
            f"设置汇率：{rate}\n"
            f"设置费率：{fee}%\n"
            f"中介佣金：{com}%"
        )
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"设置失败：{e}")

@bot.message_handler(func=lambda m: re.match(r'^(\+|加)\d+(\.\d+)?', m.text))
def handle_amount(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    m = msg.text.strip()
    amt = float(re.findall(r'\d+(\.\d+)?', m)[0])
    name = msg.from_user.username or msg.from_user.first_name or '匿名'

    cur, rate, fee, com = get_settings(chat_id, user_id)
    now = datetime.now()
    cursor.execute("""
        INSERT INTO transactions(
            chat_id, user_id, name, amount,
            rate, fee_rate, commission_rate,
            currency, date, message_id
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat_id, user_id, name, amt, rate, fee, com, cur, now, msg.message_id))
    conn.commit()

    summary = show_summary(chat_id, user_id)
    bot.reply_to(msg, f"✅ 已入款 +{amt} ({cur})\n{summary}")

# ─── 4. 启动轮询 ───────────────────────────────────────────────────────────
bot.remove_webhook()
bot.infinity_polling()
