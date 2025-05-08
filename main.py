# tg_account_bot/main.py
import os
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telebot import types

# === 环境变量 ===
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
bot = telebot.TeleBot(TOKEN)

# === 数据库连接 ===
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# === 建表 ===
cursor.execute("""
CREATE TABLE IF NOT EXISTS records (
  id SERIAL PRIMARY KEY,
  user_id BIGINT,
  username TEXT,
  amount FLOAT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (
  user_id BIGINT PRIMARY KEY,
  currency TEXT DEFAULT 'RMB',
  rate FLOAT DEFAULT 7.0,
  fee FLOAT DEFAULT 0.0,
  commission FLOAT DEFAULT 0.0
);
""")
conn.commit()

# === 固定菜单按钮 ===
def get_reply_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("▶️ Start", "💱 设置交易")
    markup.row("📖 指令大全", "🔄 计算重启")
    markup.row("❓ 需要帮助", "🛠 定制机器人")
    return markup

# === 获取用户设定 ===
def get_user_setting(user_id):
    cursor.execute("SELECT * FROM settings WHERE user_id=%s", (user_id,))
    setting = cursor.fetchone()
    if not setting:
        cursor.execute("INSERT INTO settings (user_id) VALUES (%s) RETURNING *", (user_id,))
        conn.commit()
        setting = cursor.fetchone()
    return setting

# === 汇总格式 ===
def get_summary(user_id):
    cursor.execute("SELECT SUM(amount) as total, COUNT(*) as count FROM records WHERE user_id=%s", (user_id,))
    result = cursor.fetchone()
    total = result['total'] or 0
    count = result['count'] or 0
    setting = get_user_setting(user_id)
    real_amount = total * (1 - setting['fee'] / 100)
    usdt_amount = real_amount / setting['rate'] if setting['rate'] else 0
    commission = total * (setting['commission'] / 100)
    return f"""
📊 今日统计：
已入款（{count}笔）：{total:.2f} ({setting['currency']})
汇率：{setting['rate']}
费率：{setting['fee']}%
中介佣金：{setting['commission']}%

应下发：{real_amount:.2f} {setting['currency']} | {usdt_amount:.2f} USDT
已下发：0.0 {setting['currency']} | 0.0 USDT
未下发：{real_amount:.2f} {setting['currency']} | {usdt_amount:.2f} USDT
中介佣金应下发：{commission:.2f} USDT
"""

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "欢迎使用TG记账机器人！", reply_markup=get_reply_menu())

@bot.message_handler(func=lambda msg: msg.text == "💱 设置交易")
def setting(message):
    bot.send_message(message.chat.id, "格式如下：\n设置货币：RMB\n设置汇率：9\n设置费率：2\n中介佣金：0.5")

@bot.message_handler(func=lambda msg: msg.text == "📖 指令大全")
def help_cmds(message):
    bot.send_message(message.chat.id, "🧾 指令大全：\n设置货币：RMB\n设置汇率：9\n设置费率：2\n中介佣金：0.5\n+1000（入账）")

@bot.message_handler(func=lambda msg: msg.text == "🔄 计算重启")
def reset(message):
    cursor.execute("DELETE FROM records WHERE user_id=%s", (message.from_user.id,))
    conn.commit()
    bot.reply_to(message, "✅ 今日记录已清空。")

@bot.message_handler(func=lambda msg: msg.text == "❓ 需要帮助")
def help_link(message):
    bot.send_message(message.chat.id, "加入群组获取帮助：https://t.me/yourgroup")

@bot.message_handler(func=lambda msg: msg.text == "🛠 定制机器人")
def custom_link(message):
    bot.send_message(message.chat.id, "联系管理员定制：https://t.me/yourgroup")

@bot.message_handler(func=lambda msg: msg.text.startswith("设置货币："))
def set_currency(message):
    value = message.text.split("：", 1)[1].strip().upper()
    cursor.execute("UPDATE settings SET currency=%s WHERE user_id=%s", (value, message.from_user.id))
    conn.commit()
    bot.reply_to(message, f"设置成功 ✅\n货币：{value}")

@bot.message_handler(func=lambda msg: msg.text.startswith("设置汇率："))
def set_rate(message):
    try:
        value = float(message.text.split("：", 1)[1])
        cursor.execute("UPDATE settings SET rate=%s WHERE user_id=%s", (value, message.from_user.id))
        conn.commit()
        bot.reply_to(message, f"设置成功 ✅\n汇率：{value}")
    except:
        bot.reply_to(message, "请输入正确格式，如：设置汇率：9")

@bot.message_handler(func=lambda msg: msg.text.startswith("设置费率："))
def set_fee(message):
    try:
        value = float(message.text.split("：", 1)[1])
        cursor.execute("UPDATE settings SET fee=%s WHERE user_id=%s", (value, message.from_user.id))
        conn.commit()
        bot.reply_to(message, f"设置成功 ✅\n费率：{value}%")
    except:
        bot.reply_to(message, "请输入正确格式，如：设置费率：2")

@bot.message_handler(func=lambda msg: msg.text.startswith("中介佣金："))
def set_commission(message):
    try:
        value = float(message.text.split("：", 1)[1])
        cursor.execute("UPDATE settings SET commission=%s WHERE user_id=%s", (value, message.from_user.id))
        conn.commit()
        bot.reply_to(message, f"设置成功 ✅\n中介佣金：{value}%")
    except:
        bot.reply_to(message, "请输入正确格式，如：中介佣金：0.5")

@bot.message_handler(func=lambda msg: msg.text.strip().startswith("+"))
def add_amount(message):
    try:
        amount = float(message.text.strip("+ "))
        user = message.from_user
        cursor.execute("INSERT INTO records (user_id, username, amount) VALUES (%s, %s, %s)", (user.id, user.first_name, amount))
        conn.commit()
        setting = get_user_setting(user.id)
        real_amount = amount * (1 - setting['fee'] / 100)
        usdt = real_amount / setting['rate'] if setting['rate'] else 0
        commission = amount * setting['commission'] / 100 if setting['commission'] else 0
        now = datetime.now().strftime("%d-%m-%Y\n%H:%M:%S")
        reply = f"✅ 已入款 +{amount:.2f} ({setting['currency']})\n🕓 {now}\n📌 {amount:.2f} * {(1 - setting['fee']/100):.2f} / {setting['rate']} = {usdt:.2f} {user.first_name}"
        if setting['commission']:
            reply += f"\n📌 {amount:.2f} * {setting['commission']}% = {commission:.2f}（中介佣金）"
        reply += f"\n{get_summary(user.id)}"
        bot.reply_to(message, reply)
    except:
        bot.reply_to(message, "格式错误，请输入 +金额，如 +1000")

bot.remove_webhook()
print("🤖 Bot polling started...")
bot.polling()
