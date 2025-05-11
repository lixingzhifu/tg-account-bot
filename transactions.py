# transactions.py

import re
from datetime import datetime, timedelta
from telebot import types
from main import bot, cursor, conn  # 复用主程序里的 bot 实例 和 数据库连接

def get_settings(chat_id, user_id):
    """从 settings 表里取当前配置，没配置就返回 None"""
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        return None, 0, 0, 0
    return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']

def format_time(dt):
    """把 UTC 时间转换到 +8 时区并格式化为 HH:MM:SS"""
    local = dt + timedelta(hours=8)
    return local.strftime("%H:%M:%S")

@bot.message_handler(func=lambda m: re.match(r'^(?:入笔|入|[+])\s*\d+', m.text or ''))
def handle_add(message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    # 1) 先取设置
    currency, rate, fee, comm = get_settings(chat_id, user_id)
    if rate == 0:
        return bot.reply_to(
            message,
            "⚠️ 请先发送“设置交易指令”并填写汇率，才能入笔"
        )

    # 2) 解析金额和用户名
    amount = float(re.findall(r"\d+\.?\d*", message.text)[0])
    name = message.from_user.username \
           or message.from_user.first_name \
           or "匿名"

    # 3) 存入 transactions
    now = datetime.utcnow()
    cursor.execute(
        """
        INSERT INTO transactions
           (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (chat_id, user_id, name, amount,
         rate, fee, comm, currency,
         now, message.message_id)
    )
    conn.commit()

    # 4) 拿到刚插入的自增 ID
    cursor.execute(
        "SELECT CURRVAL(pg_get_serial_sequence('transactions','id')) AS last_id"
    )
    last_id = cursor.fetchone()['last_id']

    # 5) 计算应下发和佣金
    net_rate = (1 - fee/100)       # e.g. 0.98
    out_usdt = amount*net_rate/ rate
    com_rmb   = amount*(comm/100)  # 比如 1000*0.005=5.0
    com_usdt  = com_rmb / rate

    # 6) 统计总笔数/总金额
    cursor.execute(
        "SELECT COUNT(*) AS cnt, SUM(amount) AS total "
        "FROM transactions WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    stats = cursor.fetchone()
    cnt   = stats['cnt']
    total = float(stats['total'] or 0)

    # 7) 生成回复
    lines = [
        f"✅ 已入款 +{amount:.1f}",
        f"编号：{last_id:03d}",
        f"{last_id:03d}. {format_time(now)}  {amount:.1f}*{net_rate:.2f}/{rate:.1f} = {out_usdt:.2f}  {name}",
        f"{last_id:03d}. {format_time(now)}  {amount:.1f}*{comm/100:.3f} = {com_rmb:.1f} 【佣金】",
        "",
        f"已入款（{cnt}笔）：{total:.1f} ({currency})",
        f"总入款金额：{total:.1f} ({currency})",
        f"汇率：{rate:.1f}",
        f"费率：{fee:.1f}%",
        f"佣金：{comm:.1f}%",
        "",
        f"应下发：{total*net_rate:.1f}({currency}) | {total*net_rate/rate:.2f} (USDT)",
        f"已下发：0.0({currency}) | 0.00 (USDT)",
        f"未下发：{total*net_rate:.1f}({currency}) | {total*net_rate/rate:.2f} (USDT)",
        "",
        f"中介佣金应下发：{com_rmb:.1f}({currency}) | {com_usdt:.2f} (USDT)"
    ]
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(func=lambda m: re.match(r'^-\s*\d+', m.text or ''))
def handle_remove_last(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    cursor.execute(
        """
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s
        ORDER BY id DESC
        LIMIT 1
        """,
        (chat_id, user_id)
    )
    conn.commit()
    bot.reply_to(message, "✅ 已删除最近一笔入款记录")


@bot.message_handler(func=lambda m: re.match(r'^删除订单\s*\d+', m.text or ''))
def handle_remove_by_id(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    tid = int(re.findall(r"\d+", message.text)[0])
    cursor.execute(
        "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s AND id=%s",
        (chat_id, user_id, tid)
    )
    conn.commit()
    bot.reply_to(message, f"✅ 删除订单成功，编号：{tid}")
