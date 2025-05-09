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
bot.set_my_commands([
    BotCommand('start', '启动机器人'),
    BotCommand('trade', '设置交易'),
    BotCommand('commands', '指令大全'),
    BotCommand('reset', '计算重启'),
    BotCommand('summary', '汇总'),
    BotCommand('help', '需要帮助'),
    BotCommand('custom', '定制机器人'),
])

# --- 数据库连接与迁移 ---
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()
# 确保 transactions 表含 user_id
cursor.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS user_id BIGINT")
# 创建 settings 表
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
# 创建 transactions 表
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

# --- 工具函数 ---
def ceil2(x):
    return math.ceil(x * 100) / 100.0

# 获取设置
def get_settings(chat_id, user_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    return 'RMB', 0.0, 0.0, 0.0

# 构建汇总文本
def build_summary(chat_id, user_id):
    cursor.execute(
        'SELECT id, name, amount, rate, fee_rate, commission_rate, date FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date',
        (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total_amt = sum(r['amount'] for r in rows)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    usdt_total = ceil2(total_amt * (1 - fee/100) / rate) if rate else 0.0
    comm_usdt = ceil2(total_amt * commission/100 / rate) if rate else 0.0

    today = datetime.now().strftime('%d-%m-%Y')
    lines = []
    for r in rows:
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0.0
        line = f"{t} {r['amount']}*{(1-r['fee_rate']/100):.2f}/{r['rate']} = {usdt}  {r['name']}"
        if r['commission_rate'] > 0:
            com_amt = ceil2(r['amount'] * r['commission_rate']/100)
            line += f"\n{t} {r['amount']}*{r['commission_rate']/100} = {com_amt} 【佣金】"
        lines.append(line)
    footer = (
        f"已入款（{len(rows)}笔）：{total_amt} ({currency})\n"
        f"已下发（0笔）：0.0 (USDT)\n\n"
        f"总入款金额：{total_amt} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{ceil2(total_amt*(1-fee/100))}({currency}) | {usdt_total} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total_amt*(1-fee/100))}({currency}) | {usdt_total} (USDT)\n"
    )
    if commission > 0:
        footer += f"\n中介佣金应下发：{comm_usdt} (USDT)"
    return f"订单号：{today}\n" + "\n".join(lines) + "\n" + footer

# --- 处理器 ---
@bot.message_handler(commands=['start'])
def handle_start(msg):
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请输入 /trade 来设置交易参数或使用侧边菜单。")

@bot.message_handler(commands=['trade'])
def show_trade(msg):
    c, r, f, cm = get_settings(msg.chat.id, msg.from_user.id)
    text = (
        f"设置交易指令\n"
        f"设置货币：{c}\n"
        f"设置汇率：{r}\n"
        f"设置费率：{f}\n"
        f"中介佣金：{cm}"
    )
    bot.reply_to(msg, text)

@bot.message_handler(func=lambda m: m.text and m.text.startswith('设置交易指令'))
def set_trade(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    text = msg.text
    rate_m = re.search(r'汇率[:：]?\s*(\d+\.?\d*)', text)
    if not rate_m:
        return bot.reply_to(msg, '设置失败\n至少需要提供汇率：设置汇率：9')
    curr_m = re.search(r'货币[:：]?\s*([A-Za-z]+)', text)
    fee_m  = re.search(r'费率[:：]?\s*(\d+\.?\d*)', text)
    com_m  = re.search(r'中介佣金[:：]?\s*(\d+\.?\d*)', text)
    currency = curr_m.group(1) if curr_m else 'RMB'
    rate = float(rate_m.group(1))
    fee_rate = float(fee_m.group(1)) if fee_m else 0.0
    commission_rate = float(com_m.group(1)) if com_m else 0.0

    cursor.execute(
        'SELECT 1 FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    if cursor.fetchone():
        cursor.execute(
            'UPDATE settings SET currency=%s, rate=%s, fee_rate=%s, commission_rate=%s WHERE chat_id=%s AND user_id=%s',
            (currency, rate, fee_rate, commission_rate, chat_id, user_id)
        )
    else:
        cursor.execute(
            'INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate) VALUES(%s, %s, %s, %s, %s, %s)',
            (chat_id, user_id, currency, rate, fee_rate, commission_rate)
        )
    conn.commit()
    bot.reply_to(msg,
        f"✅ 设置成功\n"
        f"设置货币：{currency}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee_rate}%\n"
        f"中介佣金：{commission_rate}%"
    )

@bot.message_handler(func=lambda m: re.match(r'^\+\d+(?:\.\d+)?$', m.text.strip()))
def handle_deposit(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    amt = float(msg.text.lstrip('+'))
    name = msg.from_user.username or msg.from_user.first_name or '匿名'
    currency, rate, fee_rate, commission_rate = get_settings(chat_id, user_id)
    now = datetime.now()
    cursor.execute(
        'INSERT INTO transactions(chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, date) VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (chat_id, user_id, name, amt, rate, fee_rate, commission_rate, currency, now)
    )
    conn.commit()
    summary = build_summary(chat_id, user_id)
    bot.reply_to(msg, f"✅ 已入款 +{amt} ({currency})\n{summary}")

@bot.message_handler(func=lambda m: re.match(r'^-\d+(?:\.\d+)?$', m.text.strip()))
def handle_withdraw(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    amt = float(msg.text.lstrip('-'))
    cursor.execute(
        'DELETE FROM transactions WHERE chat_id=%s AND user_id=%s AND amount=%s RETURNING id',
        (chat_id, user_id, amt)
    )
    deleted = cursor.fetchone()
    conn.commit()
    if deleted:
        bot.reply_to(msg, f"✅ 已删除 +{amt} 记录")
    else:
        bot.reply_to(msg, f"❌ 未找到 +{amt} 的记录")

@bot.message_handler(commands=['commands'])
def show_commands(msg):
    cmds = [c.command + ' - ' + c.description for c in bot.get_my_commands()]
    bot.reply_to(msg, "可用指令：\n" + "\n".join(cmds))

@bot.message_handler(commands=['reset'])
def handle_reset(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    cursor.execute('DELETE FROM transactions WHERE chat_id=%s AND user_id=%s', (chat_id, user_id))
    conn.commit()
    bot.reply_to(msg, "✅ 记录已清空")

@bot.message_handler(commands=['summary'])
def handle_summary(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    summary = build_summary(chat_id, user_id)
    bot.reply_to(msg, summary)

@bot.message_handler(commands=['help'])
def handle_help(msg):
    bot.reply_to(msg, "需要帮助？请加入帮助群：<链接>")

@bot.message_handler(commands=['custom'])
def handle_custom(msg):
    bot.reply_to(msg, "定制机器人？请联系管理员：<链接>")

bot.infinity_polling()
