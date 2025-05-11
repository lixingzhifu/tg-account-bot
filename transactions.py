# transactions.py
print("👉 Transactions handler loaded")
import telebot
from datetime import datetime
import math, re
from db import conn, cursor     # 假设你把 DB 相关放在 db.py
from utils import ceil2, get_settings, show_summary  # 假设工具函数都在 utils.py

bot = telebot.TeleBot(...)      # 跟 main.py 用的是同一个 bot 实例

print("👉 Transactions handler loaded")   # ★ 加这一行用来调试，看模块有没有被 import

@bot.message_handler(func=lambda m: re.match(r'^([+加]\s*\d+)|(.+\s*[+加]\s*\d+)', m.text or ''))
def handle_amount(message):
    print(f"[DEBUG] 收到了入笔：{message.text}")   # ★ 加这一行看日志
    chat_id = message.chat.id
    user_id = message.from_user.id

    # 1) 检查是否已设置汇率
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    if not rate:
        return bot.reply_to(message, "⚠️ 请先发送“设置交易”并填写汇率，才能入笔")

    # 2) 解析金额
    txt = message.text.strip()
    m = re.match(r'^([+加])\s*(\d+\.?\d*)$', txt)
    if m:
        name = message.from_user.username or message.from_user.first_name or "匿名"
        amount = float(m.group(2))
    else:
        parts = re.findall(r'(.+?)[+加]\s*(\d+\.?\d*)$', txt)
        if not parts:
            return bot.reply_to(message, "⚠️ 入笔格式错误，举例 “+1000” 或 “用户名+1000”")
        name, amount = parts[0][0].strip(), float(parts[0][1])

    # 3) 写入数据库
    now = datetime.now().strftime('%H:%M:%S')
    try:
        cursor.execute(
            '''
            INSERT INTO transactions(chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, date)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (chat_id, user_id, name, amount, rate, fee, commission, currency, now)
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(message, f"❌ 记录失败：{e}")

    # 4) 反馈给用户
    # 这里直接调用 show_summary，或是只回入笔这一笔都行
    reply =  f"✅ 已入款 +{amount} ({currency})\n"
    reply += show_summary(chat_id, user_id)
    bot.reply_to(message, reply)
