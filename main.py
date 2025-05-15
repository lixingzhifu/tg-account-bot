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

# —— 入账（记录交易） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    try:
        cursor.execute("SELECT * FROM settings WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        settings = cursor.fetchone()
        if not settings:
            return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能入账。")

        match = re.findall(r'[\+入笔]*([0-9]+(\.\d+)?)', msg.text)
        if not match:
            return bot.reply_to(msg, "❌ 无效的入账格式。请输入有效的金额，示例：+1000 或 入1000")

        amount = float(match[0][0])

        currency = settings['currency']
        rate = settings['rate']
        fee_rate = settings['fee_rate']
        commission_rate = settings['commission_rate']

        amount_after_fee = amount * (1 - fee_rate / 100)  # 扣除手续费后的金额
        amount_in_usdt = round(amount_after_fee / rate, 2)  # 转换为 USDT
        commission_rmb = round(amount * (commission_rate / 100), 2)  # 佣金（人民币）
        commission_usdt = round(commission_rmb / rate, 2)  # 佣金（USDT）

        # 获取当前时间（马来西亚时区）
        malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
        time_now = datetime.now(malaysia_tz).strftime('%H:%M:%S')

        # 生成编号（简单的序列号）
        cursor.execute("SELECT COUNT(*) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        transaction_count = cursor.fetchone()['count'] + 1
        transaction_id = str(transaction_count).zfill(3)

        issued_amount = 0.0  # 目前没有已下发金额
        unissued_amount = amount_after_fee  # 初始未下发金额等于应下发金额

        # 插入交易记录
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, message_id, deducted_amount)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, msg.from_user.username, amount, rate, fee_rate, commission_rate, currency, msg.message_id, amount_after_fee))
        conn.commit()

        # 获取已入款总数，并确保值为float类型
        cursor.execute("SELECT SUM(amount) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        total_amount = float(cursor.fetchone()['sum'] or 0)  # 确保是float类型

        # 获取已下发金额（我们使用deducted_amount字段表示已下发金额）
        cursor.execute("SELECT SUM(deducted_amount) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        total_issued = float(cursor.fetchone()['sum'] or 0)  # 确保是float类型

        # 获取未下发金额
        total_unissued = total_amount - total_issued  # 已入款 - 已下发 = 未下发

        # 计算应下发金额（未下发金额）
        total_pending = total_unissued  # 总金额减去已下发金额即为应下发金额

        # 生成返回信息
        result = (
            f"✅ 已入款 +{amount} ({currency})\n\n"
            f"编号：{transaction_id}\n\n"
            f"{transaction_id}. {time_now} {amount} * {1 - fee_rate / 100} / {rate} = {amount_in_usdt}  {msg.from_user.username}\n"
        )

        if commission_rate > 0:
            result += (
                f"{transaction_id}. {time_now} {amount} * {commission_rate / 100} = {commission_rmb} 【佣金】\n\n"
            )

        result += (
            f"已入款（{transaction_count}笔）：{total_amount} ({currency})\n\n"
            f"总入款金额：{total_amount} ({currency})\n"
            f"汇率：{rate}\n"
            f"费率：{fee_rate}%\n"
        )

        if commission_rate > 0:
            result += f"佣金：{commission_rmb} ({currency}) | {commission_usdt} USDT\n\n"
        else:
            result += "佣金：0.0 (RMB) | 0.0 USDT\n\n"

        result += (
            f"应下发：{amount_after_fee} ({currency}) | {amount_in_usdt} (USDT)\n"
            f"已下发：{total_issued} ({currency}) | 0.00 (USDT)\n"
            f"未下发：{total_unissued} ({currency}) | {amount_in_usdt} (USDT)\n\n"
        )

        if commission_rate > 0:
            result += f"中介佣金应下发：{commission_rmb} ({currency}) | {commission_usdt} (USDT)\n"

        bot.reply_to(msg, result)

    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 启动轮询 —— #
if __name__ == '__main__':
    bot.remove_webhook()  # 确保没有 webhook
    bot.infinity_polling()  # 永久轮询
