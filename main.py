import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
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
  date             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  message_id       BIGINT,
  amount_after_fee DOUBLE PRECISION,
  amount_in_base_currency DOUBLE PRECISION
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
    # 必须先贴“设置交易指令”
    if '设置交易指令' not in text:
        return bot.reply_to(msg,
            "请按下面格式发送：\n"
            "设置交易指令\n"
            "设置汇率：0\n"
            "设置费率：0\n"
            "中介佣金：0.0"
        )

    # 提取参数
    try:
        rate     = float(re.search(r'设置汇率[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
        fee      = float(re.search(r'设置费率[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
        comm     = float(re.search(r'中介佣金[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
    except Exception:
        return bot.reply_to(msg,
            "❌ 参数解析失败，请务必按格式填：\n"
            "设置交易指令\n"
            "设置汇率：0\n"
            "设置费率：0\n"
            "中介佣金：0.0"
        )

    chat_id = msg.chat.id
    user_id = msg.from_user.id

    # 写入数据库
    try:
        cursor.execute("""
        INSERT INTO settings (chat_id, user_id, rate, fee_rate, commission_rate)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (chat_id, user_id) DO UPDATE
          SET rate             = EXCLUDED.rate,
              fee_rate         = EXCLUDED.fee_rate,
              commission_rate  = EXCLUDED.commission_rate;
        """, (chat_id, user_id, rate, fee, comm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 成功反馈
    bot.reply_to(msg,
        "✅ 设置成功\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}\n"
        f"中介佣金：{comm}"
    )

# —— 入账（记录交易） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    # 获取用户的 chat_id 和 user_id
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    # 检查是否已经设置交易参数
    cursor.execute("SELECT * FROM settings WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    settings = cursor.fetchone()
    if not settings:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能入账。")

    # 使用更严格的正则来提取金额
    match = re.findall(r'[\+入笔]*([0-9]+(\.\d+)?)', msg.text)
    if not match:
        return bot.reply_to(msg, "❌ 无效的入账格式。请输入有效的金额，示例：+1000 或 入1000")

    # 提取金额并转换为浮动类型
    amount = float(match[0][0])  # 提取并转换金额

    # 获取当前设置的交易参数
    rate = settings['rate']
    fee_rate = settings['fee_rate']
    commission_rate = settings['commission_rate']

    # 计算下发金额和佣金
    amount_after_fee = amount * (1 - fee_rate / 100)  # 扣除费率后的金额
    amount_in_base_currency = amount_after_fee / rate  # 换算成基础货币金额
    commission_rmb = amount * (commission_rate / 100)
    commission_in_base_currency = commission_rmb / rate  # 佣金换算成基础货币

    # 存储入账记录
    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, 
                                  amount_after_fee, amount_in_base_currency, commission_rmb, commission_usdt)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'RMB', %s, %s, %s, %s)
        """, (chat_id, user_id, msg.from_user.username, amount, rate, fee_rate, commission_rate, 
              amount_after_fee, amount_in_base_currency, commission_rmb, commission_in_base_currency))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 返回入账信息
    bot.reply_to(msg, 
        f"✅ 已入款 +{amount} (RMB)\n"
        f"编号：{str(id)}\n"
        f"{str(id)}. {str(datetime.now().strftime('%H:%M:%S'))} {amount} * {fee_rate / 100} / {rate} = {amount_in_base_currency} RMB linlin131313\n"
        f"{str(id)}. {str(datetime.now().strftime('%H:%M:%S'))} {amount} * {commission_rate / 100} = {commission_rmb} 【佣金】\n"
        f"已入款（{str(id)}笔）：{amount} (RMB)\n"
        f"总入款金额：{total_amount} (RMB)\n"
        f"汇率：{rate}\n"
        f"费率：{fee_rate}%\n"
        f"佣金：{commission_rmb} (RMB) | {commission_in_base_currency} (USDT)"
    )

# 启动轮询
if __name__ == '__main__':
    bot.remove_webhook()      # 确保没有 webhook
    bot.infinity_polling()    # 永久轮询
