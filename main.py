# handlers.py
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot

# —— 配置 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = TeleBot(TOKEN)

# 建立数据库连接
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# helper：取设置
def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if not row:
        return None
    return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]

# 处理“设置交易指令”正文
@bot.message_handler(func=lambda m: m.text and m.text.strip().startswith("设置交易指令"))
def handle_set_trade(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    text = msg.text.replace("：", ":")
    # 逐行提取
    currency = rate = fee = commission = None
    for line in text.splitlines():
        if line.startswith("设置货币"):
            currency = line.split(":",1)[1].strip().upper()
        elif line.startswith("设置汇率"):
            try: rate = float(re.findall(r"\d+\.?\d*", line)[0])
            except: pass
        elif line.startswith("设置费率"):
            try: fee = float(re.findall(r"\d+\.?\d*", line)[0])
            except: pass
        elif line.startswith("中介佣金"):
            try: commission = float(re.findall(r"\d+\.?\d*", line)[0])
            except: pass

    # 校验
    if not (currency and rate is not None and fee is not None and commission is not None):
        return bot.reply_to(msg, "❌ 设置错误，请检查格式，必须包含：\n"
                                "设置货币：X\n设置汇率：数字\n设置费率：数字\n中介佣金：数字")

    # 存库（假设已有 settings 表，并且主键(chat_id,user_id)已建好）
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE SET
                currency = EXCLUDED.currency,
                rate = EXCLUDED.rate,
                fee_rate = EXCLUDED.fee_rate,
                commission_rate = EXCLUDED.commission_rate
        """, (chat_id, user_id, currency, rate, fee, commission))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败，请联系管理员\n错误：{e}")

    # 回复成功
    reply = (
        "✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}%\n"
        f"中介佣金：{commission}%"
    )
    bot.reply_to(msg, reply)
