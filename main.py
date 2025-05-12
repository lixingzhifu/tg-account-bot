import os
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
import re
from datetime import datetime

# 环境变量
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# 初始化
bot = TeleBot(TOKEN)

# 数据库连接
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 初始化数据库表格
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
  id SERIAL PRIMARY KEY,
  chat_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  amount DOUBLE PRECISION NOT NULL,
  rate DOUBLE PRECISION NOT NULL,
  fee_rate DOUBLE PRECISION NOT NULL,
  commission_rate DOUBLE PRECISION NOT NULL,
  amount_after_fee DOUBLE PRECISION,
  amount_in_base_currency DOUBLE PRECISION,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

# /start 和 菜单
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton('/trade'), types.KeyboardButton('设置交易'))
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择菜单：", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == '记账')
def cmd_start_alias(msg):
    cmd_start(msg)

# 设置交易
@bot.message_handler(func=lambda m: m.text.startswith('/trade') or m.text.startswith('设置交易'))
def cmd_set_trade(msg):
    text = msg.text.strip()
    if '设置交易指令' not in text:
        return bot.reply_to(msg, "请按下面格式发送：\n设置交易指令\n设置汇率：0\n设置费率：0\n中介佣金：0.0")

    try:
        rate = float(re.search(r'设置汇率[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
        fee = float(re.search(r'设置费率[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([0-9]+(?:\.[0-9]+)?)', text).group(1))
    except Exception:
        return bot.reply_to(msg, "❌ 参数解析失败，请务必按格式填：\n设置交易指令\n设置汇率：0\n设置费率：0\n中介佣金：0.0")

    chat_id = msg.chat.id
    user_id = msg.from_user.id

    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, rate, fee_rate, commission_rate)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (chat_id, user_id) DO UPDATE
          SET rate = EXCLUDED.rate,
              fee_rate = EXCLUDED.fee_rate,
              commission_rate = EXCLUDED.commission_rate;
        """, (chat_id, user_id, rate, fee, comm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    bot.reply_to(msg, f"✅ 设置成功\n设置汇率：{rate}\n设置费率：{fee}\n中介佣金：{comm}")

# 入账（记录交易）
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    cursor.execute("SELECT * FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    settings = cursor.fetchone()
    if not settings:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能入账。")

    match = re.findall(r'[\+入笔]*([0-9]+(\.\d+)?)', msg.text)
    if not match:
        return bot.reply_to(msg, "❌ 无效的入账格式。请输入有效的金额，示例：+1000 或 入1000")

    amount = float(match[0][0])
    rate = settings['rate']
    fee_rate = settings['fee_rate']
    commission_rate = settings['commission_rate']

    amount_after_fee = amount * (1 - fee_rate / 100)
    amount_in_base_currency = round(amount_after_fee / rate, 2)
    commission_rmb = round(amount * (commission_rate / 100), 2)
    commission_in_base_currency = round(commission_rmb / rate, 2)

    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, amount, rate, fee_rate, commission_rate, amount_after_fee, amount_in_base_currency)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, amount, rate, fee_rate, commission_rate, amount_after_fee, amount_in_base_currency))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    bot.reply_to(msg, f"✅ 已入款 +{amount} (RMB)\n编号：001\n"
                      f"001. {datetime.now().strftime('%H:%M:%S')} {amount} * {1 - fee_rate / 100} = {amount_after_fee} linlin131313\n"
                      f"001. {datetime.now().strftime('%H:%M:%S')} {amount} * {commission_rate / 100} = {commission_rmb} 【佣金】\n\n"
                      f"已入款（1笔）：{amount} (RMB)\n"
                      f"总入款金额：{amount} (RMB)\n"
                      f"汇率：{rate}\n"
                      f"费率：{fee_rate}%\n"
                      f"佣金：{commission_rmb} (RMB) | {commission_in_base_currency} (USDT)\n\n"
                      f"应下发：{amount_after_fee} (RMB) | {amount_in_base_currency} (USDT)\n"
                      f"已下发：0.0 (RMB) | 0.00 (USDT)\n"
                      f"未下发：{amount_after_fee} (RMB) | {amount_in_base_currency} (USDT)\n"
                      f"中介佣金应下发：{commission_rmb} (RMB) | {commission_in_base_currency} (USDT)")

# 删除订单
@bot.message_handler(func=lambda m: m.text.startswith('-') or m.text.startswith('删除订单'))
def handle_delete(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    cursor.execute("SELECT * FROM transactions WHERE chat_id = %s AND user_id = %s ORDER BY created_at DESC LIMIT 1", (chat_id, user_id))
    transaction = cursor.fetchone()

    if not transaction:
        return bot.reply_to(msg, "❌ 没有找到最近一笔交易记录。")

    cursor.execute("DELETE FROM transactions WHERE id = %s", (transaction['id'],))
    conn.commit()

    bot.reply_to(msg, f"✅ 删除订单成功，编号：{transaction['id']}")

# 启动轮询
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling()
