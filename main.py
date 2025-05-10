# handlers.py
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types

# ——— 配置 ——— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = TeleBot(TOKEN)

# ——— 数据库连接 ——— #
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# ——— 建表（只运行一次也无害） ——— #
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


# ——— 辅助：取设置 ——— #
def get_settings(chat_id, user_id):
    cursor.execute("""
        SELECT currency, rate, fee_rate, commission_rate 
        FROM settings 
        WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else None


# ——— /start 和 “记账” 启动菜单 ——— #
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text and m.text.strip() == "记账")
def handle_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("💱 设置交易", "📘 指令大全")
    kb.row("🔁 计算重启", "📊 汇总")
    kb.row("❓ 需要帮助", "🛠️ 定制机器人")
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)


# ——— “设置交易” 或 `/trade` 显示模板 ——— #
@bot.message_handler(func=lambda m: m.text and m.text.strip() in ["设置交易", "/trade", "💱 设置交易"])
def handle_trade_menu(msg):
    tmpl = (
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(msg, tmpl)


# ——— 真正解析“设置交易指令” ——— #
@bot.message_handler(func=lambda m: m.text and m.text.startswith("设置交易指令"))
def handle_set_trade(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    text = msg.text.replace("：", ":")
    # 提取四项
    currency = rate = fee = commission = None
    for line in text.splitlines():
        if line.startswith("设置货币"):
            currency = line.split(":",1)[1].strip().upper()
        elif line.startswith("设置汇率"):
            try:
                rate = float(re.findall(r"\d+\.?\d*", line)[0])
            except:
                pass
        elif line.startswith("设置费率"):
            try:
                fee = float(re.findall(r"\d+\.?\d*", line)[0])
            except:
                pass
        elif line.startswith("中介佣金"):
            try:
                commission = float(re.findall(r"\d+\.?\d*", line)[0])
            except:
                pass

    # 校验
    if not (currency and rate is not None and fee is not None and commission is not None):
        return bot.reply_to(msg,
            "❌ 设置错误，请按格式填写：\n"
            "设置交易指令\n"
            "设置货币：RMB\n"
            "设置汇率：数字\n"
            "设置费率：数字\n"
            "中介佣金：数字"
        )

    # 写入数据库
    try:
        cursor.execute("""
            INSERT INTO settings (chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (chat_id,user_id) DO UPDATE SET
                currency=EXCLUDED.currency,
                rate=EXCLUDED.rate,
                fee_rate=EXCLUDED.fee_rate,
                commission_rate=EXCLUDED.commission_rate
        """, (chat_id, user_id, currency, rate, fee, commission))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 回复成功
    bot.reply_to(msg,
        "✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}%\n"
        f"中介佣金：{commission}%"
    )


# ——— 启动 Polling ——— #
if __name__ == "__main__":
    bot.remove_webhook()
    bot.infinity_polling()
