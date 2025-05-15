import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
import pytz
from datetime import datetime

# —— 环境变量 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# —— Bot 实例 —— #
bot = TeleBot(TOKEN)

# —— 数据库连接 —— #
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 初始化建表（只会创建，不会覆盖） —— #
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
  id               SERIAL PRIMARY KEY,
  chat_id          BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  name             TEXT    NOT NULL,
  amount           DOUBLE PRECISION NOT NULL,
  rate             DOUBLE PRECISION NOT NULL,
  fee_rate         DOUBLE PRECISION NOT NULL,
  commission_rate  DOUBLE PRECISION NOT NULL,
  currency         TEXT    NOT NULL,
  date             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  message_id       BIGINT,
  status           TEXT DEFAULT 'pending', -- 状态字段
  deducted_amount  DOUBLE PRECISION DEFAULT 0.0, -- 扣除金额
  issued_amount    DOUBLE PRECISION DEFAULT 0.0, -- 已下发金额
  unissued_amount  DOUBLE PRECISION DEFAULT 0.0 -- 未下发金额
);
""")
conn.commit()

# —— /start & “记账” 命令 —— #
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton('/trade'), types.KeyboardButton('设置交易'))
    bot.reply_to(msg,
        "欢迎使用 LX 记账机器人 ✅\n"
        "请选择菜单：",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == '记账')
def cmd_start_alias(msg):
    cmd_start(msg)

# —— 设置交易配置 —— #
@bot.message_handler(func=lambda m: re.match(r'^(/trade|设置交易)', m.text or ''))
def cmd_set_trade(msg):
    text = msg.text.strip()
    if '设置交易指令' not in text:
        return bot.reply_to(msg,
            "请按下面格式发送：\n"
            "设置交易指令\n"
            "设置货币：RMB\n"
            "设置汇率：0\n"
            "设置费率：0\n"
            "中介佣金：0.0"
        )

    try:
        currency = re.search(r'设置货币[:：]\s*([^\s\n]+)', text).group(1)
        rate = float(re.search(r'设置汇率[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
        fee = float(re.search(r'设置费率[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
    except Exception:
        return bot.reply_to(msg, "❌ 参数解析失败，请务必按格式填：\n设置交易指令\n设置货币：RMB\n设置汇率：0\n设置费率：0\n中介佣金：0.0")

    chat_id = msg.chat.id
    user_id = msg.from_user.id

    try:
        cursor.execute("""
        INSERT INTO settings (chat_id, user_id, currency, rate, fee_rate, commission_rate)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (chat_id, user_id) DO UPDATE
          SET currency = EXCLUDED.currency,
              rate = EXCLUDED.rate,
              fee_rate = EXCLUDED.fee_rate,
              commission_rate = EXCLUDED.commission_rate;
        """, (chat_id, user_id, currency, rate, fee, comm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    bot.reply_to(msg, f"✅ 设置成功\n设置货币：{currency}\n设置汇率：{rate}\n设置费率：{fee}\n中介佣金：{comm}")

# —— 计算重启 —— #
@bot.message_handler(commands=['calculate_reset', 'reset'])
def cmd_reset_calculations(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    try:
        # 删除当前用户所有的 transactions，彻底清零
        cursor.execute(
            "DELETE FROM transactions WHERE chat_id = %s AND user_id = %s",
            (chat_id, user_id)
        )
        conn.commit()
        bot.reply_to(msg, "✅ 记录已清零！所有交易数据已删除，从头开始计算。")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 重置失败：{e}")

又跑回来这个.....刚才不是解决了吗

❌ 存储失败：column "issued\_amount" of relation "transactions" does not exist
LINE 4: ...           currency, message\_id, deducted\_amount, issued\_amo...

# —— 启动轮询 —— #
if __name__ == '__main__':
    bot.remove_webhook()  # 确保没有 webhook
    bot.infinity_polling()  # 永久轮询
