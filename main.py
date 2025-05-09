import os
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import math
import re
from telebot import types

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 初始化表
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT PRIMARY KEY,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0
)''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    name TEXT,
    amount DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    fee_rate DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date TEXT
)''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS payouts (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    amount DOUBLE PRECISION,
    currency TEXT,
    date TEXT
)''')

conn.commit()

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id):
    cursor.execute('SELECT * FROM settings WHERE chat_id=%s', (chat_id,))
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

def show_summary(chat_id):
    currency, rate, fee, commission = get_settings(chat_id)
    cursor.execute('SELECT * FROM transactions WHERE chat_id=%s', (chat_id,))
    tx = cursor.fetchall()
    cursor.execute('SELECT * FROM payouts WHERE chat_id=%s', (chat_id,))
    pays = cursor.fetchall()

    total = sum(t['amount'] for t in tx)
    after_fee = total * (1 - fee / 100)
    usdt_total = ceil2(after_fee / rate) if rate else 0
    commission_total = ceil2(total * commission / 100)
    commission_usdt = ceil2(commission_total / rate) if rate else 0
    paid_rmb = sum(p['amount'] for p in pays)
    paid_usdt = ceil2(paid_rmb * (1 - fee / 100) / rate) if rate else 0

    reply = f"\n已入款（{len(tx)}笔）：{total} ({currency})"
    reply += f"\n已下发（{len(pays)}笔）：{paid_usdt} (USDT)\n"
    reply += f"\n总入款金额：{total} ({currency})"
    reply += f"\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n"
    reply += f"\n应下发：{ceil2(after_fee)} ({currency}) | {usdt_total} (USDT)"
    reply += f"\n已下发：{paid_rmb} ({currency}) | {paid_usdt} (USDT)"
    reply += f"\n未下发：{ceil2(after_fee - paid_rmb)} ({currency}) | {ceil2(usdt_total - paid_usdt)} (USDT)"
    if commission > 0:
        reply += f"\n\n中介佣金应下发：{commission_total} ({currency}) | {commission_usdt} (USDT)"
    return reply

# /start 按钮菜单
@bot.message_handler(commands=['start'])
def handle_start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add("设置汇率", "汇总", "删除最后一笔", "下发 1000")
    bot.send_message(message.chat.id, "欢迎使用 LX 记账机器人 ✅\n请选择操作或输入命令：", reply_markup=markup)

# 设置命令识别
@bot.message_handler(func=lambda m: m.text.startswith("设置"))
def set_config(message):
    chat_id = message.chat.id
    text = message.text.replace("：", ":").replace(" ", "").upper()
    currency = rate = fee = commission = None
    for line in text.split('\n'):
        if '货币' in line:
            currency = re.findall(r'[A-Z]+', line)[0]
        elif '汇率' in line:
            rate = float(re.search(r'(\d+\.?\d*)', line).group(1))
        elif '费率' in line:
            fee = float(re.search(r'(\d+\.?\d*)', line).group(1))
        elif '佣金' in line:
            commission = float(re.search(r'(\d+\.?\d*)', line).group(1))
    if rate:
        cursor.execute('''
            INSERT INTO settings(chat_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET
                currency=EXCLUDED.currency,
                rate=EXCLUDED.rate,
                fee_rate=EXCLUDED.fee_rate,
                commission_rate=EXCLUDED.commission_rate
        ''', (chat_id, currency or 'RMB', rate, fee or 0, commission or 0))
        conn.commit()
        bot.reply_to(message, f"✅ 设置成功\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%")

# 入账命令
@bot.message_handler(func=lambda m: re.match(r'^[+加]', m.text.strip()))
def add_amount(message):
    chat_id = message.chat.id
    name = message.from_user.first_name or "匿名"
    match = re.match(r'(.+)?[+加]\s*(\d+\.?\d*)', message.text.strip())
    if not match:
        return
    if match.group(1) and not match.group(1).strip().isdigit():
        name = match.group(1).strip()
    amount = float(match.group(2))
    currency, rate, fee, commission = get_settings(chat_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO transactions(chat_id, name, amount, rate, fee_rate, commission_rate, currency, date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (chat_id, name, amount, rate, fee, commission, currency, now))
    conn.commit()
    bot.reply_to(message, f"✅ {name} 入款 +{amount} {currency}\n" + show_summary(chat_id))

# 删除最新入账
@bot.message_handler(func=lambda m: re.match(r'^[-减]\s*\d+', m.text.strip()))
def delete_last(message):
    chat_id = message.chat.id
    cursor.execute('SELECT id FROM transactions WHERE chat_id=%s ORDER BY id DESC LIMIT 1', (chat_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute('DELETE FROM transactions WHERE id=%s', (row['id'],))
        conn.commit()
        bot.reply_to(message, "✅ 最后一笔入账已删除。\n" + show_summary(chat_id))
    else:
        bot.reply_to(message, "⚠️ 没有记录可删除")

# 下发记录
@bot.message_handler(func=lambda m: m.text.startswith("下发"))
def record_payout(message):
    chat_id = message.chat.id
    try:
        amt = float(re.search(r'下发\s*(\d+\.?\d*)', message.text).group(1))
        currency, _, _, _ = get_settings(chat_id)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('INSERT INTO payouts(chat_id, amount, currency, date) VALUES (%s, %s, %s, %s)',
                       (chat_id, amt, currency, now))
        conn.commit()
        bot.reply_to(message, f"✅ 已下发 {amt} {currency}\n" + show_summary(chat_id))
    except:
        bot.reply_to(message, "❌ 格式错误，下发失败。请用：下发 1000")

# 汇总指令
@bot.message_handler(func=lambda m: m.text.strip() == "汇总")
def show_all(message):
    bot.reply_to(message, show_summary(message.chat.id))

# 启动机器人
print("🤖 Bot 正在运行中（Polling）...")
bot.infinity_polling()
