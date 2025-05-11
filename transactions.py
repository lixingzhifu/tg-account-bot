# transactions.py

import re
from datetime import datetime
from main import bot
from db import conn, cursor
from utils import ceil2, now_ml, get_settings, format_time, show_summary

print("👉 Transactions handler loaded")

@bot.message_handler(func=lambda m: re.match(r'^[+]\s*\d+(\.\d+)?$', m.text or ''))
def handle_add(m):
    chat,user = m.chat.id, m.from_user.id
    currency, rate, fee, comm = get_settings(chat,user)
    if rate == 0:
        return bot.reply_to(m, "⚠️ 请先 /trade 设置汇率后再入笔")
    amt = float(re.findall(r'\d+\.?\d*', m.text)[0])
    name = m.from_user.username or m.from_user.first_name or '匿名'
    now = now_ml()
    cursor.execute("""
      INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat,user,name,amt,rate,fee,comm,currency,now))
    conn.commit()
    cursor.execute("SELECT CURRVAL(pg_get_serial_sequence('transactions','id')) AS last_id")
    lid = cursor.fetchone()["last_id"]
    return bot.reply_to(m,
      f"✅ 已入款 +{amt}\n编号：{lid}\n"
      + show_summary(chat,user)
    )

@bot.message_handler(func=lambda m: m.text.strip() == '-')
def handle_del_last(m):
    chat,user = m.chat.id, m.from_user.id
    cursor.execute("""
      DELETE FROM transactions
      WHERE chat_id=%s AND user_id=%s
      ORDER BY id DESC LIMIT 1
    """,(chat,user))
    conn.commit()
    return bot.reply_to(m, "✅ 已删除最近一笔")

@bot.message_handler(func=lambda m: m.text.startswith('删除订单'))
def handle_del_id(m):
    chat,user = m.chat.id, m.from_user.id
    parts = m.text.split()
    if len(parts)!=2 or not parts[1].isdigit():
        return bot.reply_to(m,"❌ 格式：删除订单 001")
    tid = int(parts[1])
    cursor.execute("""
      DELETE FROM transactions
      WHERE chat_id=%s AND user_id=%s AND id=%s
    """,(chat,user,tid))
    if cursor.rowcount:
        conn.commit()
        return bot.reply_to(m,f"✅ 删除订单成功，编号：{tid:03d}")
    else:
        return bot.reply_to(m,"⚠️ 未找到该编号")
