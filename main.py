import telebot
import os
from datetime import datetime
import math
import psycopg2
from psycopg2.extras import RealDictCursor
import re

# ===== 配置你的 Token 和数据库地址 =====
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

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
    cursor.execute('SELECT * FROM settings WHERE chat_id=%s', (chat_id,))
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "欢迎使用 LX 记账机器人 ✅\n请输入 +1000 或者 设置汇率 等命令来开始使用。")

@bot.message_handler(func=lambda m: m.text.lower().startswith("设置"))
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
    if rate:
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
        bot.reply_to(message, f"设置成功\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%")

@bot.message_handler(func=lambda m: re.match(r'^[+加]\s*\d+', m.text.strip()))
def add_amount(message):
    chat_id = message.chat.id
    name = message.from_user.first_name or '匿名'
    amount = float(re.findall(r'\d+\.?\d*', message.text)[0])
    currency, rate, fee, commission = get_settings(chat_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO transactions(chat_id, name, amount, rate, fee_rate, commission_rate, currency, date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (chat_id, name, amount, rate, fee, commission, currency, now))
    conn.commit()
    bot.reply_to(message, f"✅ {name} 入款 +{amount} {currency}")

print("Bot is polling...")
bot.infinity_polling()
