import os
import re
import math
import psycopg2
import telebot
from datetime import datetime
from psycopg2.extras import RealDictCursor

# —— 环境变量 —— #
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# —— 初始化 Bot & DB —— #
bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 建表 —— #
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id          BIGINT PRIMARY KEY,
    currency         TEXT              NOT NULL DEFAULT 'RMB',
    rate             DOUBLE PRECISION  NOT NULL DEFAULT 0,
    fee_rate         DOUBLE PRECISION  NOT NULL DEFAULT 0,
    commission_rate  DOUBLE PRECISION  NOT NULL DEFAULT 0
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id              SERIAL PRIMARY KEY,
    chat_id         BIGINT    NOT NULL,
    name            TEXT      NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    rate            DOUBLE PRECISION NOT NULL,
    fee_rate        DOUBLE PRECISION NOT NULL,
    commission_rate DOUBLE PRECISION NOT NULL,
    currency        TEXT      NOT NULL,
    date            TIMESTAMP NOT NULL,
    message_id      BIGINT
)
''')
conn.commit()

# —— 工具函数 —— #
def ceil2(x):
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id):
    cursor.execute('SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s', (chat_id,))
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    else:
        return 'RMB', 0, 0, 0

def set_settings(chat_id, currency, rate, fee, commission):
    # 先 UPDATE
    cursor.execute('''
        UPDATE settings
           SET currency=%s, rate=%s, fee_rate=%s, commission_rate=%s
         WHERE chat_id=%s
    ''', (currency, rate, fee, commission, chat_id))
    if cursor.rowcount == 0:
        # 如果没更新到，再 INSERT
        cursor.execute('''
            INSERT INTO settings(chat_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s, %s, %s, %s, %s)
        ''', (chat_id, currency, rate, fee, commission))
    conn.commit()

def build_summary(chat_id):
    cursor.execute('SELECT * FROM transactions WHERE chat_id=%s ORDER BY date', (chat_id,))
    rows = cursor.fetchall()
    currency, rate, fee, commission = get_settings(chat_id)

    total = sum(r['amount'] for r in rows)
    converted_total = ceil2(total * (1 - fee/100) / rate) if rate else 0
    commission_rmb = ceil2(total * commission/100)
    commission_usdt = ceil2(commission_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(rows, 1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = ceil2(r['amount'] * (1 - r['fee_rate']/100))
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        line = f"{idx}. {t} {r['amount']}*{1 - r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}"
        lines.append(line)
        if r['commission_rate'] > 0:
            cm = ceil2(r['amount'] * r['commission_rate']/100)
            lines.append(f"{idx}. {t} {r['amount']}*{r['commission_rate']/100:.2f} = {cm} 【佣金】")

    summary = "\n".join(lines) + "\n\n"
    summary += f"已入款（{len(rows)}笔）：{total} ({currency})\n"
    summary += f"总入款金额：{total} ({currency})\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    summary += f"应下发：{ceil2(total*(1 - fee/100))}({currency}) | {converted_total} (USDT)\n"
    summary += f"已下发：0.0({currency}) | 0.0 (USDT)\n"
    summary += f"未下发：{ceil2(total*(1 - fee/100))}({currency}) | {converted_total} (USDT)\n"
    if commission > 0:
        summary += f"\n中介佣金应下发：{commission_rmb}({currency}) | {commission_usdt} (USDT)"
    return summary

# —— /start —— #
@bot.message_handler(commands=['start'])
def on_start(msg):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('💱 设置交易', '📘 指令大全')
    markup.row('🔁 计算重启', '📊 汇总')
    markup.row('❓ 需要帮助', '🛠️ 定制机器人')
    bot.send_message(msg.chat.id,
                     "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
                     reply_markup=markup)

# —— 设计一个 /trade （设置交易）命令 —— #
@bot.message_handler(commands=['trade'])
@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def show_trade_template(msg):
    tpl = (
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(msg, tpl)

# —— 真正处理 “设置交易指令” —— #
@bot.message_handler(func=lambda m: m.text and m.text.startswith('设置交易指令'))
def handle_trade(msg):
    text = m_text = msg.text.replace('：',':').strip()
    # 提取四行
    pattern = r"设置货币[:：] *([A-Za-z]+).*?设置汇率[:：] *([\d.]+).*?设置费率[:：] *([\d.]+).*?中介佣金[:：] *([\d.]+)"
    m = re.search(pattern, text, re.S)
    if not m:
        return bot.reply_to(msg, "设置错误，请检查格式\n示例：\n设置交易指令\n设置货币：RMB\n设置汇率：9\n设置费率：2\n中介佣金：0.5")
    cur, rate, fee, cm = m.groups()
    rate, fee, cm = float(rate), float(fee), float(cm)
    set_settings(msg.chat.id, cur.upper(), rate, fee, cm)
    bot.reply_to(msg,
        f"✅ 设置成功\n"
        f"设置货币：{cur.upper()}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}%\n"
        f"中介佣金：{cm}%"
    )

# —— 入笔 / 删除 —— #
@bot.message_handler(func=lambda m: re.match(r'^([+加\-])\s*(\d+(\.\d+)?)', m.text.strip()))
def handle_amount(msg):
    text = msg.text.strip()
    op, num = re.match(r'^([+加\-])\s*(\d+(\.\d+)?)', text).group(1,2)
    amt = float(num)
    cid = msg.chat.id

    # 删除：负号
    if op == '-' or op == '减':
        # 用 message_id 或 最后一笔 进行删除
        cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND message_id=%s", (cid, msg.reply_to_message.message_id if msg.reply_to_message else msg.message_id))
        conn.commit()
        return bot.reply_to(msg, f"🗑 已删除 {amt}")

    # 加笔：正号
    name = msg.from_user.username or msg.from_user.first_name or '匿名'
    cur, rate, fee, cm = get_settings(cid)
    now = datetime.now()
    after_fee = ceil2(amt * (1 - fee/100))
    usdt = ceil2(after_fee / rate) if rate else 0

    cursor.execute('''
        INSERT INTO transactions(chat_id, name, amount, rate, fee_rate, commission_rate, currency, date, message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ''', (cid, name, amt, rate, fee, cm, cur, now, msg.message_id))
    conn.commit()

    reply = f"✅ 已入款 +{amt} ({cur})\n编号：{msg.message_id}\n"
    reply += build_summary(cid)
    bot.reply_to(msg, reply)

# —— 汇总 —— #
@bot.message_handler(func=lambda m: m.text in ['/summary','汇总','📊 汇总'])
def show_summary(msg):
    bot.reply_to(msg, build_summary(msg.chat.id))

# —— 启动 —— #
bot.infinity_polling()
