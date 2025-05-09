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

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

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
conn.commit()

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id):
    cursor.execute('SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s', (chat_id,))
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

def show_summary(chat_id):
    cursor.execute('SELECT * FROM transactions WHERE chat_id=%s', (chat_id,))
    records = cursor.fetchall()
    total = sum(row['amount'] for row in records)
    currency, rate, fee, commission = get_settings(chat_id)
    converted_total = ceil2(total * (1 - fee / 100) / rate) if rate else 0
    commission_total_rmb = ceil2(total * commission / 100)
    commission_total_usdt = ceil2(commission_total_rmb / rate) if rate else 0
    reply = "欢迎使用 LX 记账机器人 ✅
请从下方菜单选择操作：""
    bot.send_message(message.chat.id, reply, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text.lower().startswith('设置'))
def set_config(message):
    chat_id = message.chat.id
    text = message.text.replace('：', ':').replace(' ', '').upper()
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
    if rate is not None:
        cursor.execute('''
            INSERT INTO settings(chat_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET
            currency = EXCLUDED.currency,
            rate = EXCLUDED.rate,
            fee_rate = EXCLUDED.fee_rate,
            commission_rate = EXCLUDED.commission_rate
        ''', (chat_id, currency or 'RMB', rate, fee or 0, commission or 0))
        conn.commit()
        bot.reply_to(message, f"设置成功 ✅\n设置货币：{currency or 'RMB'}\n设置汇率：{rate}\n设置费率：{fee or 0}%\n中介佣金：{commission or 0}%")

@bot.message_handler(func=lambda m: re.match(r'^([+加]\s*\d+)|(.+\s*[+加]\s*\d+)', m.text))
def add_transaction(message):
    chat_id = message.chat.id
    text = message.text.strip()
    match = re.match(r'^([+加])\s*(\d+\.?\d*)$', text)
    if match:
        name = message.from_user.first_name or '匿名'
        amount = float(match.group(2))
    else:
        name, amt = re.findall(r'(.+)[+加]\s*(\d+\.?\d*)', text)[0]
        name = name.strip()
        amount = float(amt)

    currency, rate, fee, commission = get_settings(chat_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''INSERT INTO transactions(chat_id, name, amount, rate, fee_rate, commission_rate, currency, date)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                   (chat_id, name, amount, rate, fee, commission, currency, now))
    conn.commit()
    bot.reply_to(message, f"✅ 已入款 +{amount} ({currency})\n日期\n" + show_summary(chat_id))

@bot.message_handler(func=lambda m: m.text.strip() in ['设置交易', '💱 设置交易'])
def handle_set_command(message):
    reply = "格式如下：\n设置货币：RMB\n设置汇率：0\n设置费率：0\n中介佣金：0"
    bot.reply_to(message, reply)

bot.infinity_polling()
