# transactions.py

import re
from datetime import datetime
import pytz
from telebot import types
from psycopg2.extras import RealDictCursor

# 导入在 main.py 中创建的 bot 实例和数据库连接 conn、cursor
from main import bot, conn
from main import cursor  # cursor already uses RealDictCursor

# 工具：拿到用户的设置
def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]
    else:
        return "RMB", 0.0, 0.0, 0.0

# 工具：生成 3 位编号（从 001 开始，按当前表总行数 +1）
def next_order_id(chat_id, user_id):
    cursor.execute(
        "SELECT COUNT(*) FROM transactions WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    count = cursor.fetchone()["count"] or 0
    return f"{count+1:03d}"

# —— 处理“+1000” 或 “名称+1000” 入笔 —— #
@bot.message_handler(func=lambda m: re.match(r'^(.+)?[+\＋]\s*\d+(\.\d+)?$', m.text.strip()))
def handle_amount(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id

    # 1) 必须先设置汇率
    currency, rate, fee_rate, comm_rate = get_settings(chat_id, user_id)
    if rate == 0:
        return bot.reply_to(msg, "❌ 请先发送“设置交易”并填写汇率，才能入笔。")

    txt = msg.text.strip()
    # 2) 判断是 “+1000” 还是 “名称+1000”
    m = re.match(r'^(?:([^\+]+))?[+\＋]\s*(\d+(\.\d+)?)$', txt)
    name = msg.from_user.username or msg.from_user.first_name or "匿名"
    if m and m.group(1):
        name = m.group(1).strip()
    amount = float(m.group(2))

    # 3) 时间戳（马来西亚时区）
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now = datetime.now(tz).strftime("%d-%m-%Y %H:%M:%S")

    # 4) 计算下发和佣金
    after_fee = amount * (1 - fee_rate/100)
    usdt = round(after_fee / rate, 2)
    comm_amount = round(amount * (comm_rate/100), 2)
    comm_usdt = round(comm_amount / rate, 2)

    # 5) 存库
    order_id = next_order_id(chat_id, user_id)
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                user_id BIGINT,
                order_id TEXT,
                name TEXT,
                amount DOUBLE PRECISION,
                rate DOUBLE PRECISION,
                fee_rate DOUBLE PRECISION,
                commission_rate DOUBLE PRECISION,
                currency TEXT,
                timestamp TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO transactions
              (chat_id, user_id, order_id, name, amount, rate, fee_rate, commission_rate, currency, timestamp)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            chat_id, user_id, order_id, name, amount,
            rate, fee_rate, comm_rate, currency, now
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 记录失败：{e}")

    # 6) 回复给用户
    reply = [
        f"✅ 已入款 +{amount:.2f} ({currency})",
        f"编号：{order_id}",
        f"1. {now.split()[1]} {amount:.2f}*{(1-fee_rate/100):.2f}/{rate:.2f} = {usdt:.2f}  {name}"
    ]
    if comm_rate > 0:
        reply.append(f"2. {now.split()[1]} {amount:.2f}*{comm_rate/100:.3f} = {comm_amount:.2f} 【佣金】")
    bot.reply_to(msg, "\n".join(reply))

# —— 删除最近一条订单 —— #
@bot.message_handler(func=lambda m: m.text.strip() in ["-","-1000"])
def handle_delete_last(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    # 找到最后一条
    cursor.execute("""
        SELECT id, order_id FROM transactions
        WHERE chat_id=%s AND user_id=%s
        ORDER BY id DESC LIMIT 1
    """, (chat_id, user_id))
    row = cursor.fetchone()
    if not row:
        return bot.reply_to(msg, "❌ 没有可删除的订单。")
    cursor.execute("DELETE FROM transactions WHERE id=%s", (row["id"],))
    conn.commit()
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{row['order_id']}")

# —— 按编号删除 —— #
@bot.message_handler(func=lambda m: m.text.startswith("删除订单"))
def handle_delete_by_id(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    parts = msg.text.split()
    if len(parts)!=2:
        return bot.reply_to(msg, "❌ 格式：删除订单 001")
    oid = parts[1]
    cursor.execute("""
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s AND order_id=%s
    """, (chat_id, user_id, oid))
    if cursor.rowcount:
        conn.commit()
        bot.reply_to(msg, f"✅ 删除订单成功，编号：{oid}")
    else:
        bot.reply_to(msg, f"❌ 找不到编号：{oid}")
