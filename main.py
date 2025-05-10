import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import math
import re
import os

TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(TOKEN)

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 初始化数据库
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
    date TEXT,
    message_id BIGINT
)''')

conn.commit()

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute('SELECT currency, rate, fee_rate, commission_rate '
                   'FROM settings WHERE chat_id=%s AND user_id=%s',
                   (chat_id, user_id))
    row = cursor.fetchone()
    if not row or row['rate'] == 0:
        return None
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate'])

def show_summary(chat_id, user_id):
    cursor.execute('SELECT * FROM transactions '
                   'WHERE chat_id=%s AND user_id=%s ORDER BY id',
                   (chat_id, user_id))
    records = cursor.fetchall()
    total = sum(r['amount'] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    converted_total = ceil2(total * (1 - fee / 100) / rate)
    commission_total_rmb = ceil2(total * (commission / 100))
    commission_total_usdt = ceil2(commission_total_rmb / rate)
    reply = ''
    for i, row in enumerate(records, 1):
        t = datetime.strptime(row['date'], '%Y-%m-%d %H:%M:%S')\
                    .strftime('%H:%M:%S')
        after_fee = row['amount'] * (1 - row['fee_rate']/100)
        usdt = ceil2(after_fee / row['rate'])
        commission_frac = row['commission_rate'] / 100  # 0.5% -> 0.005
        commission_amt = ceil2(row['amount'] * commission_frac)
        # 入款行
        reply += f"{i}. {t} {row['amount']}*{(1 - row['fee_rate']/100):.2f}/{row['rate']} = {usdt}  {row['name']}\n"
        # 佣金行（只有 rate>0 且 commission_rate>0 才显示）
        if row['commission_rate'] > 0:
            reply += (
                f"{i}. {t} {row['amount']}*{commission_frac:.4f} = "
                f"{commission_amt} 【佣金】\n"
            )
    reply += f"\n已入款（{len(records)}笔）：{total} ({currency})\n"
    reply += f"已下发（0笔）：0.0 (USDT)\n\n"
    reply += (
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    )
    reply += (
        f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({currency}) | "
        f"{converted_total} (USDT)\n"
    )
    if commission > 0:
        reply += (
            f"\n中介佣金应下发：{commission_total_rmb}({currency}) | "
            f"{commission_total_usdt} (USDT)"
        )
    return reply

@bot.message_handler(commands=['start'])
def handle_start(message):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('💱 设置交易', '📘 指令大全')
    markup.row('🔁 计算重启', '📊 汇总')
    markup.row('❓ 需要帮助', '🛠️ 定制机器人')
    bot.send_message(
        message.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
        reply_markup=markup
    )

@bot.message_handler(commands=['id'])
def handle_id(message):
    bot.reply_to(
        message,
        f"你的 chat_id 是：{message.chat.id}\n你的 user_id 是：{message.from_user.id}"
    )

@bot.message_handler(func=lambda m: m.text and '设置交易' in m.text)
def handle_set_command(message):
    bot.reply_to(
        message,
        "设置交易指令\n设置货币：RMB\n设置汇率：0\n设置费率：0\n中介佣金：0"
    )

@bot.message_handler(func=lambda m: m.text and '设置交易指令' in m.text)
def set_trade_config(message):
    data = message.text.replace('：',':').split('\n')[1:]
    params = {'currency':None,'rate':None,'fee':0,'commission':0}
    errors = []
    for line in data:
        line = line.strip().replace(' ','')
        if line.startswith('设置货币:'):
            params['currency'] = line.split(':',1)[1]
        elif line.startswith('设置汇率:'):
            try: params['rate'] = float(line.split(':',1)[1])
            except: errors.append("汇率格式错误")
        elif line.startswith('设置费率:'):
            try: params['fee'] = float(line.split(':',1)[1])
            except: errors.append("费率格式错误")
        elif line.startswith('中介佣金:'):
            try: params['commission'] = float(line.split(':',1)[1])
            except: errors.append("中介佣金请设置数字")
    if errors:
        return bot.reply_to(message, "设置错误\n" + "\n".join(errors))
    if not params['rate']:
        return bot.reply_to(message, "设置错误，至少需要提供汇率")
    chat_id,user_id = message.chat.id, message.from_user.id
    cursor.execute('''
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
    ''',(
        chat_id,user_id,
        params['currency'] or 'RMB',
        params['rate'],
        params['fee'],
        params['commission']
    ))
    conn.commit()
    bot.reply_to(
        message,
        f"✅ 设置成功\n设置货币：{params['currency'] or 'RMB'}\n"
        f"设置汇率：{params['rate']}\n"
        f"设置费率：{params['fee']}%\n"
        f"中介佣金：{params['commission']}%"
    )

@bot.message_handler(func=lambda m: m.text and re.match(r'^[\+\-加]\s*\d+(\.\d*)?$', m.text))
def handle_amount(message):
    # 必须先有设置
    s = get_settings(message.chat.id, message.from_user.id)
    if not s:
        return bot.reply_to(
            message,
            "请先发送 “设置交易” 并填写汇率，才能入笔"
        )
    bot.send_message(message.chat.id, f"[DEBUG] 收到了入笔：{message.text.strip()}")
    # …后续插入 transaction 并回复同上 show_summary 的格式…

bot.remove_webhook()
bot.infinity_polling()
