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
        # 1. 读取用户设置
        cursor.execute(
            "SELECT * FROM settings WHERE chat_id = %s AND user_id = %s",
            (chat_id, user_id)
        )
        settings = cursor.fetchone()
        if not settings:
            return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能入账。")

        # 2. 解析金额
        match = re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)
        if not match:
            return bot.reply_to(msg, "❌ 无效的入账格式。示例：+1000 或 入1000")
        amount = float(match[0])

        # 3. 获取汇率等参数
        currency        = settings['currency']
        rate            = settings['rate']
        fee_rate        = settings['fee_rate']
        commission_rate = settings['commission_rate']

        # 4. 计算：扣手续费、换 USDT、佣金
        amount_after_fee = amount * (1 - fee_rate / 100)
        amount_in_usdt   = round(amount_after_fee / rate, 2)
        commission_rmb   = round(amount * (commission_rate / 100), 2)
        commission_usdt  = round(commission_rmb / rate, 2)

        # 5. 时间和编号
        tz = pytz.timezone('Asia/Kuala_Lumpur')
        time_now = datetime.now(tz).strftime('%H:%M:%S')
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        cnt = cursor.fetchone()['cnt'] + 1
        tid = str(cnt).zfill(3)

        # 6. 插入这笔交易：deducted_amount 是“应下发”，issued_amount 初始 0
        cursor.execute("""
            INSERT INTO transactions
              (chat_id, user_id, name, amount, rate, fee_rate, commission_rate,
               currency, message_id, deducted_amount, issued_amount)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0.0)
        """, (
            chat_id, user_id, msg.from_user.username,
            amount, rate, fee_rate, commission_rate,
            currency, msg.message_id, amount_after_fee
        ))
        conn.commit()

        # 7. 汇总所有交易：
        #   total_amount       = SUM(amount)
        #   total_pending_rmb  = SUM(deducted_amount)  （所有应下发）
        #   total_issued_rmb   = SUM(issued_amount)   （所有已下发）
        cursor.execute(
            "SELECT SUM(amount)           AS sum_amt, "
            "       SUM(deducted_amount)  AS sum_pending, "
            "       SUM(issued_amount)    AS sum_issued "
            "FROM transactions "
            "WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        row = cursor.fetchone()
        total_amount       = float(row['sum_amt']     or 0)
        total_pending_rmb  = float(row['sum_pending'] or 0)
        total_issued_rmb   = float(row['sum_issued']  or 0)
        total_unissued_rmb = total_pending_rmb - total_issued_rmb

        # 换算 USDT
        total_pending_usdt  = round(total_pending_rmb  / rate, 2)
        total_issued_usdt   = round(total_issued_rmb   / rate, 2)
        total_unissued_usdt = round(total_unissued_rmb / rate, 2)

        # 8. 生成回复
        result = (
            f"✅ 已入款 +{amount} ({currency})\n\n"
            f"编号：{tid}\n\n"
            f"{tid}. {time_now} {amount} * {1 - fee_rate/100} / {rate} = {amount_in_usdt}  {msg.from_user.username}\n"
        )
        if commission_rate > 0:
            result += (
                f"{tid}. {time_now} {amount} * {commission_rate/100} = {commission_rmb} 【佣金】\n\n"
            )
        result += (
            f"已入款（{cnt}笔）：{total_amount} ({currency})\n\n"
            f"总入款金额：{total_amount} ({currency})\n"
            f"汇率：{rate}\n"
            f"费率：{fee_rate}%\n"
        )
        if commission_rate > 0:
            result += f"佣金：{commission_rmb} ({currency}) | {commission_usdt} USDT\n\n"
        else:
            result += "佣金：0.0 (RMB) | 0.0 USDT\n\n"
        result += (
            f"应下发：{total_pending_rmb} ({currency}) | {total_pending_usdt} (USDT)\n"
            f"已下发：{total_issued_rmb} ({currency}) | {total_issued_usdt} (USDT)\n"
            f"未下发：{total_unissued_rmb} ({currency}) | {total_unissued_usdt} (USDT)\n\n"
        )
        result += (
            f"中介佣金应下发：{commission_rmb} ({currency}) | {commission_usdt} (USDT)\n"
        )

        bot.reply_to(msg, result)

    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 启动轮询 —— #
if __name__ == '__main__':
    bot.remove_webhook()  # 确保没有 webhook
    bot.infinity_polling()  # 永久轮询
