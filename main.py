from keep_alive import keep_alive
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import math
import re
import os
from flask import Flask, request
from telebot.types import Update, ReplyKeyboardMarkup, KeyboardButton

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
reply = f"\n已入款（{len(records)}笔）：{total} ({currency})"
reply += f"\n已下发（0笔）：0.0 (USDT)\n"
reply += f"\n总入款金额：{total} ({currency})"
reply += f"\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n"
reply += f"\n应下发：{ceil2(total * (1 - fee / 100))}({currency}) | {converted_total} (USDT)"
reply += f"\n已下发：0.0({currency}) | 0.0 (USDT)"
reply += f"\n未下发：{ceil2(total * (1 - fee / 100))}({currency}) | {converted_total} (USDT)"
if commission > 0:
reply += f"\n\n中介佣金应下发：{commission_total_rmb}({currency}) | {commission_total_usdt} (USDT)"
return reply

@bot.message_handler(commands=['start'])
def handle_start(message):
markup = ReplyKeyboardMarkup(resize_keyboard=True)
markup.row(KeyboardButton('设置汇率'), KeyboardButton('汇总'))
markup.row(KeyboardButton('删除最后一笔'), KeyboardButton('下发 1000'))
bot.send_message(message.chat.id,
"欢迎使用 LX 记账机器人 ✅\n"
"请选择操作或输入命令：\n\n"
"📌 指令大全：\n"
"➕ 入笔：+1000 或 加1000\n"
"➖ 删除最后一笔：删除 或 删除最后一笔\n"
"📤 下发：下发1000\n"
"🧮 汇总：汇总 或 总金额\n"
"⚙️ 设置：设置汇率、费率、佣金（例：设置汇率 7.2）\n"
"🔁 重置：计算重启 或 清空\n"
"🆘 帮助与定制机器人：联系群组",
reply_markup=markup
)

@bot.message_handler(func=lambda m: m.text.lower().startswith('设置'))
def set_config(message):
chat_id = message.chat.id
text = message.text.replace('：', ':').replace(' ', '').upper()
currency = rate = fee = commission = None
for line in text.split('\n'):
if '货币' in line:
currency = re.findall(r'[A-Z]+', line)[0]
elif '汇率' in line:
rate = float(re.search(r'(\d+.?\d*)', line).group(1))
elif '费率' in line:
fee = float(re.search(r'(\d+.?\d*)', line).group(1))
elif '佣金' in line:
commission = float(re.search(r'(\d+.?\d*)', line).group(1))
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
bot.reply_to(message, f"设置成功\n固定汇率：{rate}\n固定费率：{fee}%\n中介佣金：{commission}%")

@bot.message_handler(func=lambda m: re.match(r'^[+加]\s*\d+', m.text.strip()))
def add_transaction(message):
chat_id = message.chat.id
text = message.text.strip()
amount = float(re.findall(r'\d+.?\d*', text)[0])
name = message.from_user.first_name or '匿名'
currency, rate, fee, commission = get_settings(chat_id)
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
cursor.execute('''INSERT INTO transactions(chat_id, name, amount, rate, fee_rate, commission_rate, currency, date)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
(chat_id, name, amount, rate, fee, commission, currency, now))
conn.commit()
bot.reply_to(message, f"✅ 已入款 +{amount} ({currency})\n日期\n" + show_summary(chat_id))

@bot.message_handler(func=lambda m: m.text.lower() in ['汇总', '总金额'])
def handle_summary(message):
bot.reply_to(message, show_summary(message.chat.id))

@bot.message_handler(func=lambda m: m.text.startswith('下发'))
def handle_payout(message):
try:
amt = float(re.search(r'\d+.?\d*', message.text).group(0))
bot.reply_to(message, f"✅ 格式正确，下发金额：{amt}")
except:
bot.reply_to(message, "❌ 格式错误，下发失败。请用：下发 1000")

@bot.message_handler(func=lambda m: m.text.lower() in ['删除', '删除最后一笔'])
def delete_last(message):
chat_id = message.chat.id
cursor.execute('DELETE FROM transactions WHERE id=(SELECT id FROM transactions WHERE chat_id=%s ORDER BY id DESC LIMIT 1)', (chat_id,))
conn.commit()
bot.reply_to(message, "✅ 已删除最后一笔记录")

@bot.message_handler(func=lambda m: m.text.lower() in ['计算重启', '清空'])
def reset_all(message):
chat_id = message.chat.id
cursor.execute('DELETE FROM transactions WHERE chat_id=%s', (chat_id,))
conn.commit()
bot.reply_to(message, "✅ 所有入账记录已清空")

app = Flask(name)

@app.route('/')
def index():
return "Bot is running."

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
update = Update.de_json(request.stream.read().decode("utf-8"))
bot.process_new_updates([update])
return "ok"

keep_alive()

WEBHOOK_URL = f"https://grateful-fulfillment-production.up.railway.app/{TOKEN}"
bot.remove_webhook()
bot.set_webhook(url=WEBHOOK_URL)
