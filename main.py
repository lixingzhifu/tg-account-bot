# main.py
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
from datetime import datetime, timedelta

# ── 环境变量 ──
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ── Bot & 数据库连接 ──
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# ── 建表（只会创建一次，不会覆盖已有数据） ──
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id         BIGINT NOT NULL,
  user_id         BIGINT NOT NULL,
  currency        TEXT    NOT NULL,
  rate            DOUBLE PRECISION NOT NULL,
  fee_rate        DOUBLE PRECISION NOT NULL,
  commission_rate DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
  id              SERIAL PRIMARY KEY,
  chat_id         BIGINT NOT NULL,
  user_id         BIGINT NOT NULL,
  name            TEXT    NOT NULL,
  amount          DOUBLE PRECISION NOT NULL,
  rate            DOUBLE PRECISION NOT NULL,
  fee_rate        DOUBLE PRECISION NOT NULL,
  commission_rate DOUBLE PRECISION NOT NULL,
  currency        TEXT    NOT NULL,
  date            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  message_id      BIGINT
);
""")
conn.commit()

# ── 工具函数 ──
def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    return 'RMB', 0.0, 0.0, 0.0

def format_trade_template(currency, rate, fee, commission):
    return (
        "设置交易指令\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}\n"
        f"中介佣金：{commission}"
    )

# ── /start 或 “记账” ──
@bot.message_handler(commands=['start','记账'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('设置交易')
    bot.reply_to(
        msg,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=kb
    )

# ── “设置交易” ／ /trade 显示当前配置 ──
@bot.message_handler(func=lambda m: m.text=='设置交易' or m.text=='/trade')
def cmd_show_trade(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    bot.reply_to(
        msg,
        format_trade_template(currency, rate, fee, commission)
    )

# ── 解析用户发来的“设置交易指令”五行 ──
@bot.message_handler(func=lambda m: m.text.startswith('设置交易指令'))
def cmd_set_trade(msg):
    lines = msg.text.strip().splitlines()
    if len(lines) != 5:
        return bot.reply_to(
            msg,
            "请按以下格式发送：\n" +
            format_trade_template('RMB', 0, 0, 0)
        )
    try:
        currency   = lines[1].split('：',1)[1].strip()
        rate       = float(lines[2].split('：',1)[1])
        fee        = float(lines[3].split('：',1)[1])
        commission = float(lines[4].split('：',1)[1])
    except:
        return bot.reply_to(msg, "❌ 设置错误，请检查数字格式")
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
            VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              currency=EXCLUDED.currency,
              rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate,
              commission_rate=EXCLUDED.commission_rate
        """, (msg.chat.id, msg.from_user.id, currency, rate, fee, commission))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")
    bot.reply_to(
        msg,
        "✅ 设置成功\n"
        f"货币：{currency}\n"
        f"汇率：{rate}\n"
        f"费率：{fee}%\n"
        f"中介佣金：{commission}%"
    )

if __name__ == "__main__":
    bot.remove_webhook()
    bot.infinity_polling()
