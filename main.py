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

# 连接数据库
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 初始化表结构（settings 和 transactions），含 user_id 列和联合主键
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
''')
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
    date TIMESTAMP,
    message_id BIGINT
);
''')
conn.commit()

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    else:
        return 'RMB', 0, 0, 0

def show_summary(chat_id, user_id):
    cursor.execute(
        'SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id',
        (chat_id, user_id)
    )
    records = cursor.fetchall()
    total = sum(r['amount'] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)

    converted_total = ceil2(total * (1 - fee/100) / rate) if rate else 0
    commission_total_rmb = ceil2(total * commission/100)
    commission_total_usdt = ceil2(commission_total_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(records, start=1):
        t = r['date'].strftime('%d-%m-%Y %H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        lines.append(f"{idx}. {t}  {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  @{r['name']}")
        if r['commission_rate'] > 0:
            comm_amt = r['amount'] * r['commission_rate']/100
            lines.append(f"   {idx}. {t}  {r['amount']}*{r['commission_rate']/100:.2f} = {ceil2(comm_amt)} 【佣金】")

    reply = "\n".join(lines) + "\n\n"
    reply += f"已入款（{len(records)}笔）：{total} ({currency})\n"
    reply += f"已下发（0笔）：0.0 (USDT)\n\n"
    reply += f"总入款金额：{total} ({currency})\n"
    reply += f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    reply += f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
    reply += f"已下发：0.0({currency}) | 0.0 (USDT)\n"
    reply += f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
    if commission > 0:
        reply += f"\n中介佣金应下发：{commission_total_rmb}({currency}) | {commission_total_usdt} (USDT)"
    return reply

@bot.message_handler(commands=['start','reset'])
def handle_start(message):
    if message.text == '/reset':
        cursor.execute(
            'DELETE FROM transactions WHERE chat_id=%s AND user_id=%s',
            (message.chat.id, message.from_user.id)
        )
        conn.commit()
        bot.reply_to(message, "🔄 已清空记录")
        return

    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('💱 设置交易','📘 指令大全')
    markup.row('🔁 计算重启','📊 汇总')
    markup.row('❓ 需要帮助','🛠️ 定制机器人')
    bot.reply_to(message, "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：", reply_markup=markup)

@bot.message_handler(commands=['id'])
def handle_id(message):
    bot.reply_to(
        message, 
        f"你的 chat_id：{message.chat.id}\n你的 user_id：{message.from_user.id}"
    )

@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易','/trade'])
def ask_set(message):
    bot.reply_to(
        message,
        "设置交易指令\n设置货币：RMB\n设置汇率：0\n设置费率：0\n中介佣金：0"
    )

@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def set_trade_config(message):
    chat_id, user_id = message.chat.id, message.from_user.id
    lines = message.text.replace('：',':').splitlines()
    currency=rate=fee=commission=None
    errs=[]
    for L in lines:
        if L.startswith('设置货币'):
            currency = L.split(':',1)[1].strip().upper()
        elif L.startswith('设置汇率'):
            try: rate = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('汇率格式错误')
        elif L.startswith('设置费率'):
            try: fee = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('费率格式错误')
        elif L.startswith('中介佣金'):
            try: commission = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('中介佣金请设置数字')

    if errs:
        return bot.reply_to(message, "设置错误\n"+'\n'.join(errs))
    if rate is None:
        return bot.reply_to(message, "设置错误，缺少汇率，请至少设置汇率")

    cursor.execute('''
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
    ''',(chat_id,user_id,currency or 'RMB',rate,fee or 0,commission or 0))
    conn.commit()

    bot.reply_to(
        message,
        f"✅ 设置成功\n设置货币：{currency}\n设置汇率：{rate}\n设置费率：{fee or 0}%\n中介佣金：{commission or 0}%"
    )

@bot.message_handler(func=lambda m: re.match(r'^[\+加]\s*\d+(\.\d+)?', m.text or ''))
def handle_amount(message):
    chat_id, user_id = message.chat.id, message.from_user.id
    text = message.text.strip()
    match = re.match(r'^([\+加])\s*(\d+(\.\d+)?)$', text)
    if match:
        amount = float(match.group(2))
        name = message.from_user.username or message.from_user.first_name or '匿名'
    else:
        parts = re.findall(r'(.+?)[\+加]\s*(\d+(\.\d+)?)', text)
        name, amount = parts[0][0].strip(), float(parts[0][1])

    cur, rate, fee, comm = get_settings(chat_id, user_id)
    now = datetime.now()
    cursor.execute('''
        INSERT INTO transactions
        (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ''',(
        chat_id,user_id,name,amount,rate,fee,comm,cur,now,message.message_id
    ))
    conn.commit()

    summary = show_summary(chat_id, user_id)
    bot.reply_to(
        message,
        f"✅ 已入款 +{amount} ({cur})\n编号：{message.message_id}\n" + summary
    )

bot.remove_webhook()
bot.infinity_polling()
