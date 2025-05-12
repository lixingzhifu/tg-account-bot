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
  status           TEXT DEFAULT 'pending'  -- 状态字段
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

# —— 入账（记录交易） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id

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

    amount_after_fee = amount * (1 - fee_rate / 100)
    amount_in_usdt = round(amount_after_fee / rate, 2)
    commission_rmb = round(amount * (commission_rate / 100), 2)
    commission_usdt = round(commission_rmb / rate, 2)

    # 获取当前时间（马来西亚时区）
    malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
    time_now = datetime.now(malaysia_tz).strftime('%H:%M:%S')

    # 生成编号（简单的序列号）
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    transaction_count = cursor.fetchone()['count'] + 1
    transaction_id = str(transaction_count).zfill(3)

    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, msg.from_user.username, amount, rate, fee_rate, commission_rate, currency))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 获取已入款总数
    cursor.execute("SELECT SUM(amount) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    total_amount = cursor.fetchone()['sum']

    # 生成返回信息，增加空格和格式调整
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
        f"已下发：0.0 ({currency}) | 0.00 (USDT)\n"
        f"未下发：{amount_after_fee} ({currency}) | {amount_in_usdt} (USDT)\n\n"
    )

    if commission_rate > 0:
        result += f"中介佣金应下发：{commission_rmb} ({currency}) | {commission_usdt} (USDT)\n"

    bot.reply_to(msg, result)

# —— 删除订单 —— #
@bot.message_handler(func=lambda m: re.match(r'^(删除订单|减| -)\d+$', m.text or ''))
def delete_order(msg):
    text = msg.text.strip()
    match = re.match(r'^(删除订单|减| -)(\d+)$', text)
    if not match:
        return bot.reply_to(msg, "❌ 无效的删除指令，请输入正确的编号，如：删除订单011 或 -1000。")

    order_id = int(match.group(2))
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    try:
        cursor.execute("DELETE FROM transactions WHERE chat_id = %s AND user_id = %s AND id = %s", (chat_id, user_id, order_id))
        conn.commit()
        bot.reply_to(msg, f"✅ 删除订单成功，编号：{order_id}")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 删除订单失败：{e}")

# —— 下发功能 —— #
@bot.message_handler(func=lambda m: re.match(r'^下发(\d+(\.\d+)?)$', m.text or ''))
def handle_withdrawal(msg):
    # 提取下发金额
    match = re.match(r'^下发(\d+(\.\d+)?)$', msg.text)
    if not match:
        return bot.reply_to(msg, "❌ 无效的下发格式。请输入有效的金额，示例：下发1000")

    withdrawal_amount = float(match.group(1))

    chat_id = msg.chat.id
    user_id = msg.from_user.id

    # 检查用户是否有设置交易
    cursor.execute("SELECT * FROM settings WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    settings = cursor.fetchone()
    if not settings:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能下发。")

    # 获取当前的应下发金额
    cursor.execute("""
        SELECT SUM(amount) FROM transactions WHERE chat_id = %s AND user_id = %s AND status = 'pending'
    """, (chat_id, user_id))
    total_amount_to_send = cursor.fetchone()['sum']

    if withdrawal_amount > total_amount_to_send:
        return bot.reply_to(msg, f"❌ 你请求的下发金额 ({withdrawal_amount} RMB) 超过了应下发金额 ({total_amount_to_send} RMB)。")

    # 更新应下发和已下发金额
    cursor.execute("""
        UPDATE transactions
        SET status = 'sent'
        WHERE chat_id = %s AND user_id = %s AND status = 'pending'
        LIMIT 1
    """, (chat_id, user_id))
    conn.commit()

    # 反馈下发信息
    bot.reply_to(msg, f"✅ 已成功下发 {withdrawal_amount} (RMB)\n已更新应下发和未下发金额。")

    # 更新后重新显示交易状态
    cursor.execute("""
        SELECT SUM(amount) FROM transactions WHERE chat_id = %s AND user_id = %s AND status = 'pending'
    """, (chat_id, user_id))
    remaining_to_send = cursor.fetchone()['sum']

    bot.reply_to(msg, f"当前剩余应下发金额：{remaining_to_send} RMB")

