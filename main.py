import os
import re
import math
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telebot.types import BotCommand

# --- 配置 ---
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# --- 初始化 ---
bot = telebot.TeleBot(TOKEN)
# 在侧边固定显示命令菜单
bot.set_my_commands([
    BotCommand('start', '启动机器人'),
    BotCommand('trade', '设置交易'),
    BotCommand('commands', '指令大全'),
    BotCommand('reset', '计算重启'),
    BotCommand('summary', '汇总'),
    BotCommand('help', '需要帮助'),
    BotCommand('custom', '定制机器人'),
])

# 数据库连接
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# --- 建表 ---
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    user_id BIGINT,
    name TEXT,
    amount DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    fee_rate DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date TIMESTAMP
)''')
conn.commit()

# --- 辅助函数 ---
def ceil2(n):
    return math.ceil(n * 100) / 100.0

# 获取配置
def get_settings(chat_id, user_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

# --- 处理器 ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(
        message,
        "欢迎使用 LX 记账机器人 ✅\n" +
        "请输入 /trade 来设置交易参数，或使用侧边菜单选择操作。"
    )

@bot.message_handler(commands=['trade'])
def handle_trade_menu(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    # 仅显示指令模板，不要“格式如下”前缀
    text = (
        f"设置交易指令\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee}\n"
        f"中介佣金：{commission}"
    )
    bot.reply_to(message, text)

@bot.message_handler(func=lambda m: m.text and m.text.strip().startswith('设置交易指令'))
def set_trade_config(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    lines = message.text.replace('：', ':').split('\n')[1:]
    params = {'currency': None, 'rate': None, 'fee_rate': None, 'commission_rate': None}
    for line in lines:
        if ':' in line:
            key, val = line.split(':', 1)
            key = key.strip()
            val = val.strip()
            if key == '设置货币':
                params['currency'] = val
            elif key == '设置汇率':
                try:
                    params['rate'] = float(val)
                except:
                    return bot.reply_to(message, '设置失败\n汇率格式请设置数字')
            elif key == '设置费率':
                try:
                    params['fee_rate'] = float(val)
                except:
                    return bot.reply_to(message, '设置失败\n费率格式请设置数字')
            elif key == '中介佣金':
                try:
                    params['commission_rate'] = float(val)
                except:
                    return bot.reply_to(message, '设置失败\n中介佣金请设置数字')
    # 必填检查
    if params['rate'] is None:
        return bot.reply_to(message, '设置失败\n至少需要提供汇率，例如：设置汇率：9')
    # upsert
    cursor.execute(
        'SELECT 1 FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    if cursor.fetchone():
        cursor.execute(
            'UPDATE settings SET currency=%s, rate=%s, fee_rate=%s, commission_rate=%s WHERE chat_id=%s AND user_id=%s',
            (params['currency'] or 'RMB', params['rate'], params['fee_rate'] or 0,
             params['commission_rate'] or 0, chat_id, user_id)
        )
    else:
        cursor.execute(
            'INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate) VALUES(%s,%s,%s,%s,%s,%s)',
            (chat_id, user_id, params['currency'] or 'RMB', params['rate'], params['fee_rate'] or 0, params['commission_rate'] or 0)
        )
    conn.commit()
    bot.reply_to(
        message,
        f"✅ 设置成功\n"
        f"设置货币：{params['currency']}\n"
        f"设置汇率：{params['rate']}\n"
        f"设置费率：{params['fee_rate']}\n"
        f"中介佣金：{params['commission_rate']}"
    )

# +1000 入款处理
@bot.message_handler(func=lambda m: re.match(r'^\+\d+(\.\d+)?$', m.text.strip()))
def handle_deposit(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    amount = float(message.text.lstrip('+'))
    name = message.from_user.first_name or '匿名'
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    now = datetime.now()
    cursor.execute(
        'INSERT INTO transactions(chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, date) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (chat_id, user_id, name, amount, rate, fee, commission, currency, now)
    )
    conn.commit()
    bot.reply_to(message, f"✅ 已入款 +{amount} ({currency})")

if __name__ == '__main__':
    bot.infinity_polling(timeout=60)
