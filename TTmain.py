# main.py
import pytz
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types

# —— 环境变量 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = TeleBot(TOKEN)

# —— 数据库连接 —— #
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 建表（只会创建一次，无害） —— #
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)
""")
conn.commit()

# —— 菜单启动 (/start 或 “记账”) —— #
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text and m.text.strip() == "记账")
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💱 设置交易", "📘 指令大全")
    kb.row("🔁 重启计算", "🛠️ 定制机器人")
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

# —— 显示“设置交易”模板 —— #
@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["设置交易", "/trade", "💱 设置交易"])
def cmd_trade_menu(msg):
    template = (
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(msg, template)

# —— 真正解析“设置交易指令” —— #
@bot.message_handler(func=lambda m: m.text and m.text.startswith("设置交易指令"))
def cmd_set_trade(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    text = msg.text.replace("：", ":")
    # 提取参数
    currency = rate = fee = commission = None
    for line in text.splitlines():
        if line.startswith("设置货币"):
            currency = line.split(":",1)[1].strip().upper()
        elif line.startswith("设置汇率"):
            nums = re.findall(r"\d+\.?\d*", line)
            rate = float(nums[0]) if nums else None
        elif line.startswith("设置费率"):
            nums = re.findall(r"\d+\.?\d*", line)
            fee = float(nums[0]) if nums else None
        elif line.startswith("中介佣金"):
            nums = re.findall(r"\d+\.?\d*", line)
            commission = float(nums[0]) if nums else None

    # 校验四项都必须提供
    if not all([currency, rate is not None, fee is not None, commission is not None]):
        return bot.reply_to(msg,
            "❌ 设置错误，请按格式填写：\n"
            "设置交易指令\n"
            "设置货币：RMB\n"
            "设置汇率：数字\n"
            "设置费率：数字\n"
            "中介佣金：数字"
        )

    # 存库
    try:
        cursor.execute("""
            INSERT INTO settings (chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (chat_id, user_id) DO UPDATE SET
              currency = EXCLUDED.currency,
              rate = EXCLUDED.rate,
              fee_rate = EXCLUDED.fee_rate,
              commission_rate = EXCLUDED.commission_rate
        """, (chat_id, user_id, currency, rate, fee, commission))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 成功回复
    bot.reply_to(msg,
        "✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}%\n"
        f"中介佣金：{commission}%"
    )

# —— 启动轮询 —— #
import transactions
if __name__ == "__main__":
    bot.remove_webhook()      # 确保没有 webhook
    bot.infinity_polling()    # 只启动一次
