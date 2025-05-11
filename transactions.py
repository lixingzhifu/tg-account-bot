# transactions.py
import re
from datetime import datetime
from main import bot
from db import conn, cursor
from utils import ceil2, get_settings, format_time, show_summary

print("👉 Transactions handler loaded")

@bot.message_handler(func=lambda m: re.match(r"^[+]\s*\d+", m.text or ""))
def handle_add(message):
    chat_id  = message.chat.id
    user_id  = message.from_user.id
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    if rate == 0:
        return bot.reply_to(message, "⚠️ 请先发送 “设置交易” 并填写汇率，才能入笔")

    amount = float(re.findall(r"\d+\.?\d*", message.text)[0])
    name   = message.from_user.username or message.from_user.first_name or "匿名"
    now    = datetime.utcnow()

    cursor.execute("""
        INSERT INTO transactions
          (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat_id, user_id, name, amount, rate, fee, commission, currency, now, message.message_id))
    conn.commit()

    cursor.execute("SELECT CURRVAL(pg_get_serial_sequence('transactions','id')) AS last_id")
    last_id = cursor.fetchone()["last_id"]

    return bot.reply_to(
        message,
        f"✅ 已入款 +{amount}\n"
        f"编号：{last_id}\n"
        + show_summary(chat_id, user_id)
    )

@bot.message_handler(func=lambda m: re.match(r"^-\s*\d+", m.text or ""))
def handle_remove_last(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    cursor.execute("""
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s
        ORDER BY id DESC
        LIMIT 1
    """, (chat_id, user_id))
    conn.commit()
    return bot.reply_to(message, "✅ 已删除最近一笔入款记录")

@bot.message_handler(func=lambda m: re.match(r"^删除订单\s*\d+", m.text or ""))
def handle_remove_by_id(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    tid = int(re.findall(r"\d+", message.text)[0])
    cursor.execute("""
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s AND id=%s
    """, (chat_id, user_id, tid))
    conn.commit()
    return bot.reply_to(message, f"✅ 删除订单成功，编号：{tid}")
