# main.py
import os
import re
import math
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telebot import TeleBot, types

# — 环境变量 —
TOKEN        = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = TeleBot(TOKEN)

# — 数据库连接 —
conn   = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# — 建表（如不存在则创建） —
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT     DEFAULT 'RMB',
    rate     DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    user_id BIGINT,
    name    TEXT,
    amount  DOUBLE PRECISION,
    rate    DOUBLE PRECISION,
    fee_rate       DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date     TIMESTAMP,
    message_id BIGINT
);
""")
conn.commit()

# — 工具函数 —
def ceil2(x: float) -> float:
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]
    return "RMB", 0, 0, 0

def format_time(dt: datetime) -> str:
    # 转换到马来西亚时区
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    return dt.astimezone(tz).strftime("%H:%M:%S")

def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total = sum(r["amount"] for r in rows)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    converted = ceil2(total * (1 - fee/100) / rate) if rate else 0
    comm_rmb  = ceil2(total * commission/100)
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    lines = []
    for r in rows:
        t = format_time(r["date"])
        after_fee = r["amount"] * (1 - r["fee_rate"]/100)
        usdt = ceil2(after_fee / r["rate"]) if r["rate"] else 0
        lines.append(f"{r['id']}. {t} {r['amount']}*{1 - r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r["commission_rate"] > 0:
            cm = ceil2(r["amount"] * r["commission_rate"]/100)
            lines.append(f"{r['id']}. {t} {r['amount']}*{r['commission_rate']/100:.3f} = {cm} 【佣金】")

    reply = "\n".join(lines) + "\n\n"
    reply += f"已入款（{len(rows)}笔）：{total} ({currency})\n"
    reply += f"总入款金额：{total} ({currency})\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    reply += f"应下发：{ceil2(total*(1 - fee/100))}({currency}) | {converted} (USDT)\n"
    reply += f"已下发：0.0({currency}) | 0.0 (USDT)\n"
    reply += f"未下发：{ceil2(total*(1 - fee/100))}({currency}) | {converted} (USDT)\n"
    if commission>0:
        reply += f"\n中介佣金应下发：{comm_rmb}({currency}) | {comm_usdt} (USDT)"
    return reply

# — /start & 菜单 —
@bot.message_handler(commands=["start", "记账"])
def cmd_start(msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💱 设置交易", "📘 指令大全")
    markup.row("🔁 计算重启", "📊 汇总")
    markup.row("❓ 需要帮助", "🛠️ 定制机器人")
    bot.reply_to(msg,
        "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
        reply_markup=markup
    )

@bot.message_handler(commands=["id"])
def cmd_id(msg):
    bot.reply_to(msg,
        f"你的 chat_id：{msg.chat.id}\n你的 user_id：{msg.from_user.id}"
    )

# — /trade 设置交易参数 —
@bot.message_handler(func=lambda m: m.text.strip() in ["设置交易", "💱 设置交易"])
def cmd_show_trade(m):
    bot.reply_to(m,
        "设置交易指令\n设置货币：RMB\n设置汇率：0\n设置费率：0\n中介佣金：0"
    )

@bot.message_handler(func=lambda m: "设置交易指令" in m.text)
def cmd_set_trade(m):
    text = m.text.replace("：", ":")
    chat, user = m.chat.id, m.from_user.id

    # 只有私聊 或 群组管理员 才能设置
    if m.chat.type != "private":
        member = bot.get_chat_member(chat, user)
        if not (member.status in ["administrator", "creator"]):
            return bot.reply_to(m, "❌ 你不是管理员，无权设置交易参数")

    # 解析参数
    currency = rate = fee = commission = None
    errors = []
    for line in text.split("\n"):
        if "货币" in line:
            v = line.split("货币:")[1].strip().upper()
            currency = re.sub(r"[^A-Z]", "", v)
        if "汇率" in line:
            try: rate = float(re.findall(r"\d+\.?\d*", line)[0])
            except: errors.append("汇率格式错误")
        if "费率" in line:
            try: fee = float(re.findall(r"\d+\.?\d*", line)[0])
            except: errors.append("费率格式错误")
        if "佣金" in line:
            try: commission = float(re.findall(r"\d+\.?\d*", line)[0])
            except: errors.append("中介佣金格式错误")

    if errors or rate is None:
        return bot.reply_to(m, "⚠️ 设置错误，请按格式填写，并至少提供汇率")

    # 写入数据库
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (chat_id,user_id) DO UPDATE SET
              currency=EXCLUDED.currency,
              rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate,
              commission_rate=EXCLUDED.commission_rate
        """, (chat, user, currency, rate, fee, commission))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(m, f"❌ 存储失败：{e}")

    # 成功回复
    bot.reply_to(m,
        f"✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}%\n"
        f"中介佣金：{commission}%"
    )

# — /reset 清空当前 chat 所有记录（谨慎）—
@bot.message_handler(commands=["reset"])
def cmd_reset(m):
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s", (m.chat.id, m.from_user.id))
    conn.commit()
    bot.reply_to(m, "🔄 已清空本人的所有入款记录")

# — /summary 汇总 —
@bot.message_handler(func=lambda m: m.text.strip() in ["汇总", "📊 汇总", "/summary"])
def cmd_summary(m):
    bot.reply_to(m, show_summary(m.chat.id, m.from_user.id))

# — 记录入款 / 删除订单 —
@bot.message_handler(func=lambda m: re.match(r"^([+\-]|删除订单)\s*(\w+)?\s*(\d+)", m.text))
def cmd_transactions(m):
    text = m.text.strip()
    chat, user = m.chat.id, m.from_user.id

    # 先加载设置
    currency, rate, fee, commission = get_settings(chat, user)
    if rate == 0:
        return bot.reply_to(m, "⚠️ 请先发送 “设置交易” 并填写汇率，才能入笔")

    # “+1000” 入款
    m_add = re.match(r"^[+]\s*(\d+\.?\d*)$", text)
    if m_add:
        amount = float(m_add.group(1))
        name = m.from_user.username or m.from_user.first_name
        now = datetime.utcnow()
        cursor.execute("""
            INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (chat, user, name, amount, rate, fee, commission, currency, now, m.message_id))
        conn.commit()
        return bot.reply_to(m,
            f"✅ 已入款 +{amount}\n编号：{cursor.lastrowid}\n" +
            show_summary(chat, user)
        )

    # “-” 删除最近一笔
    m_del = re.match(r"^-\s*(\d+\.?\d*)$", text)
    if m_del:
        cursor.execute("""
            DELETE FROM transactions 
            WHERE chat_id=%s AND user_id=%s
            ORDER BY id DESC LIMIT 1
        """, (chat, user))
        conn.commit()
        return bot.reply_to(m, "✅ 已删除最近一笔入款记录")

    # “删除订单001”
    m_del_id = re.match(r"^删除订单\s*(\d+)", text)
    if m_del_id:
        tid = int(m_del_id.group(1))
        cursor.execute("""
            DELETE FROM transactions
            WHERE chat_id=%s AND user_id=%s AND id=%s
        """, (chat, user, tid))
        conn.commit()
        return bot.reply_to(m, f"✅ 删除订单成功，编号：{tid}")

    # 其余不处理
    return

import transactions

if __name__ == "__main__":
    bot.remove_webhook()
    bot.infinity_polling()
