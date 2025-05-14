import os
import psycopg2
from telebot import TeleBot, types
import re
from datetime import datetime

# 获取 Telegram Token 和数据库配置
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# 数据库连接配置
parsed_url = urllib.parse.urlparse(DATABASE_URL)
conn = psycopg2.connect(
    database=parsed_url.path[1:],  # Remove leading slash
    user=parsed_url.username,
    password=parsed_url.password,
    host=parsed_url.hostname,
    port=parsed_url.port
)
cursor = conn.cursor()

# 设置机器人
bot = TeleBot(TOKEN)

# 初始化数据库
def init_db():
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            name VARCHAR(255) NOT NULL,
            amount DOUBLE PRECISION NOT NULL,
            rate DOUBLE PRECISION NOT NULL,
            fee_rate DOUBLE PRECISION NOT NULL,
            commission_rate DOUBLE PRECISION NOT NULL,
            currency VARCHAR(10) NOT NULL,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_id BIGINT,
            commission DOUBLE PRECISION,  -- 使用原来的字段
            issued_amount DOUBLE PRECISION DEFAULT 0.0, 
            unissued_amount DOUBLE PRECISION
        );
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT,
            user_id BIGINT,
            exchange_rate FLOAT,
            fee_rate FLOAT,
            commission_rate FLOAT,
            currency VARCHAR(10)
        );
    ''')
    conn.commit()

# 计算应下发、已下发金额
def calculate_deductions(amount, fee_rate, commission_rate, rate):
    # 计算扣除后的金额
    deducted_amount = amount * (1 - fee_rate / 100)
    
    # 计算佣金
    commission = deducted_amount * commission_rate / 100
    
    # 最终应下发金额
    final_amount = deducted_amount - commission
    
    # 计算应下发金额（人民币和USDT）
    exchange_final_amount = final_amount / rate
    
    return deducted_amount, commission, final_amount, exchange_final_amount

# /start 触发
@bot.message_handler(commands=['start'])
def send_welcome(msg):
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

# 设置交易配置
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

# 记录入金
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

    # 计算扣除后的金额
    amount_after_fee = amount * (1 - fee_rate / 100)
    amount_in_usdt = round(amount_after_fee / rate, 2)
    
    # 计算佣金
    commission_rmb = round(amount * (commission_rate / 100), 2)
    commission_usdt = round(commission_rmb / rate, 2)

    # 获取当前时间（马来西亚时区）
    malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
    time_now = datetime.now(malaysia_tz).strftime('%H:%M:%S')

    # 生成编号（简单的序列号）
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    transaction_count = cursor.fetchone()['count'] + 1
    transaction_id = str(transaction_count).zfill(3)

    # 计算应下发金额、已下发金额、未下发金额
    issued_amount = 0.0  # 当前没有已下发金额
    unissued_amount = amount_after_fee  # 未下发金额等于应下发金额

    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, message_id, deducted_amount, commission, final_amount, issued_amount, unissued_amount)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, msg.from_user.username, amount, rate, fee_rate, commission_rate, currency, msg.message_id, amount_after_fee, commission_rmb, amount_after_fee, issued_amount, unissued_amount))
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
        f"已下发：{issued_amount} ({currency}) | 0.00 (USDT)\n"
        f"未下发：{unissued_amount} ({currency}) | {amount_in_usdt} (USDT)\n\n"
    )

    if commission_rate > 0:
        result += f"中介佣金应下发：{commission_rmb} ({currency}) | {commission_usdt} (USDT)\n"

    bot.reply_to(msg, result)

# 启动轮询
if __name__ == '__main__':
    init_db()  # 初始化数据库
    bot.infinity_polling()  # 启动轮询，保持 bot 运行
