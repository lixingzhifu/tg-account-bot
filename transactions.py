# transactions.py
import re
from datetime import datetime
import pytz

from telebot import TeleBot
from psycopg2.extras import RealDictCursor
from db import conn, cursor      # db.py 中暴露 conn, cursor
from utils import ceil2, get_settings, format_time, show_summary  # utils.py 中统一放工具函数

bot = TeleBot()  # 和 main.py 中用的是同一个实例

print("👉 Transactions handler loaded")

@bot.message_handler(func=lambda m: re.match(r"^([+\-]|删除订单)\s*(\w+)?\s*(\d+)", m.text))
def handle_amount(message):
    print(f"[DEBUG] 收到了入笔：{message.text}")
    chat_id = message.chat.id
    user_id = message.from_user.id

    # 1) 检查是否已设置汇率
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    if rate == 0:
        return bot.reply_to(message,
            "⚠️ 请先发送 “设置交易” 并填写汇率，才能入笔"
        )

    txt = message.text.strip()

    # a) “+1000” 入款
    m_add = re.match(r"^[+]\s*(\d+\.?\d*)$", txt)
    if m_add:
        amount = float(m_add.group(1))
        name   = message.from_user.username or message.from_user.first_name or "匿名"
        now    = datetime.utcnow()
        # 插入数据库
        cursor.execute("""
            INSERT INTO transactions
              (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (chat_id, user_id, name, amount, rate, fee, commission, currency, now, message.message_id))
        conn.commit()

        # 获取刚插入的 ID
        cursor.execute("SELECT CURRVAL(pg_get_serial_sequence('transactions','id')) AS last_id")
        last_id = cursor.fetchone()["last_id"]

        # 回复
        return bot.reply_to(message,
            f"✅ 已入款 +{amount}\n"
            f"编号：{last_id}\n"
            + show_summary(chat_id, user_id)
        )

    # b) “-1000” 删除最近一笔
    m_del = re.match(r"^-\s*(\d+\.?\d*)$", txt)
    if m_del:
        cursor.execute("""
            DELETE FROM transactions
             WHERE chat_id=%s AND user_id=%s
             ORDER BY id DESC
             LIMIT 1
        """, (chat_id, user_id))
        conn.commit()
        return bot.reply_to(message, "✅ 已删除最近一笔入款记录")

    # c) “删除订单001” 按编号删除
    m_del_id = re.match(r"^删除订单\s*(\d+)", txt)
    if m_del_id:
        tid = int(m_del_id.group(1))
        cursor.execute("""
            DELETE FROM transactions
             WHERE chat_id=%s AND user_id=%s AND id=%s
        """, (chat_id, user_id, tid))
        conn.commit()
        return bot.reply_to(message, f"✅ 删除订单成功，编号：{tid}")

    # 其它不处理
    return
