import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import math
import re
import os

# ———— 1. 读取环境变量 ————
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# ———— 2. 初始化 Bot 和 数据库连接 ————
bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# ———— 3. 建表：settings 和 transactions ————
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT    DEFAULT 'RMB',
    rate DOUBLE PRECISION       DEFAULT 0,
    fee_rate DOUBLE PRECISION   DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
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
    date TEXT,
    message_id BIGINT
);
''')
conn.commit()

# ———— 4. 工具函数 ————

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id, user_id):
    """取当前用户的设置，rate=0 或不存在时返回 None"""
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate '
        'FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if not row or row['rate'] == 0:
        return None
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate'])

def show_summary(chat_id, user_id):
    """格式化汇总（仅在 /汇总 命令时调用）"""
    cursor.execute('''
        SELECT * FROM transactions
        WHERE chat_id=%s AND user_id=%s ORDER BY id
    ''', (chat_id, user_id))
    records = cursor.fetchall()
    total = sum(r['amount'] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    # 计算
    converted_total = ceil2(total * (1 - fee/100) / rate)
    commission_total_rmb = ceil2(total * (commission/100))
    commission_total_usdt = ceil2(commission_total_rmb / rate)
    # 明细
    reply = ''
    for i, row in enumerate(records, 1):
        t = datetime.strptime(row['date'], '%Y-%m-%d %H:%M:%S')\
                    .strftime('%H:%M:%S')
        after_fee = row['amount'] * (1 - row['fee_rate']/100)
        usdt = ceil2(after_fee / row['rate'])
        commission_frac = row['commission_rate']/100
        commission_amt = ceil2(row['amount'] * commission_frac)
        # 入款行
        reply += (
            f"{i}. {t} {row['amount']}*"
            f"{(1-row['fee_rate']/100):.2f}/{row['rate']} = {usdt}  {row['name']}\n"
        )
        # 佣金行
        if row['commission_rate'] > 0:
            reply += (
                f"{i}. {t} {row['amount']}*"
                f"{commission_frac:.4f} = {commission_amt} 【佣金】\n"
            )
    # 汇总尾部
    reply += (
        f"\n已入款（{len(records)}笔）：{total} ({currency})\n"
        f"已下发（0笔）：0.0 (USDT)\n\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
    )
    if commission > 0:
        reply += (
            f"\n中介佣金应下发：{commission_total_rmb}"
            f"({currency}) | {commission_total_usdt} (USDT)"
        )
    return reply

# ———— 5. 命令 & 消息处理 ————

@bot.message_handler(commands=['start'])
def cmd_start(msg):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('💱 设置交易', '📘 指令大全')
    markup.row('🔁 重启计算', '📊 汇总')
    markup.row('❓ 帮助', '🛠️ 定制')
    bot.send_message(
        msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
        reply_markup=markup
    )

@bot.message_handler(func=lambda m: m.text and '设置交易' in m.text)
def cmd_show_set_template(msg):
    bot.reply_to(
        msg,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

@bot.message_handler(func=lambda m: m.text and '设置交易指令' in m.text)
def cmd_set_trade(msg):
    lines = msg.text.replace('：',':').split('\n')[1:]
    p = {'currency':None,'rate':None,'fee':0,'commission':0}
    errs = []
    for L in lines:
        L = L.strip().replace(' ','')
        if L.startswith('设置货币:'):
            p['currency'] = L.split(':',1)[1]
        elif L.startswith('设置汇率:'):
            try: p['rate'] = float(L.split(':',1)[1])
            except: errs.append("汇率格式错误")
        elif L.startswith('设置费率:'):
            try: p['fee'] = float(L.split(':',1)[1])
            except: errs.append("费率格式错误")
        elif L.startswith('中介佣金:'):
            try: p['commission'] = float(L.split(':',1)[1])
            except: errs.append("中介佣金请设置数字")
    if errs:
        return bot.reply_to(msg, "设置错误\n" + "\n".join(errs))
    if not p['rate'] or p['rate']==0:
        return bot.reply_to(msg, "设置错误，至少需要提供汇率")
    # 写入
    cursor.execute('''
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
    ''',(
        msg.chat.id, msg.from_user.id,
        p['currency'] or 'RMB',
        p['rate'], p['fee'], p['commission']
    ))
    conn.commit()
    bot.reply_to(
        msg,
        "✅ 设置成功\n"
        f"设置货币：{p['currency'] or 'RMB'}\n"
        f"设置汇率：{p['rate']}\n"
        f"设置费率：{p['fee']}%\n"
        f"中介佣金：{p['commission']}%"
    )

@bot.message_handler(func=lambda m: m.text and re.match(r'^[\+\-加]\s*\d+(\.\d*)?$', m.text))
def handle_amount(msg):
    # —— 检查已设置 —— #
    st = get_settings(msg.chat.id, msg.from_user.id)
    if not st:
        return bot.reply_to(
            msg,
            "请先发送 “设置交易” 并填写汇率，才能入笔"
        )
    currency, rate, fee, commission = st
    # —— 解析金额 —— #
    txt = msg.text.strip()
    sign = txt[0]
    amt = float(re.findall(r'\d+(\.\d*)?', txt)[0])
    if sign in ['-','减']:
        # 删除最后一笔
        cursor.execute('''
            DELETE FROM transactions
            WHERE chat_id=%s AND user_id=%s
            ORDER BY id DESC
            LIMIT 1
        ''',(msg.chat.id, msg.from_user.id))
        conn.commit()
        return bot.reply_to(msg, "✅ 最近一笔已删除")
    # —— 插入交易 —— #
    name = msg.from_user.first_name or '匿名'
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT INTO transactions(
          chat_id,user_id,name,amount,rate,fee_rate,commission_rate,
          currency,date,message_id
        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ''',(
        msg.chat.id, msg.from_user.id,
        name, amt, rate, fee, commission,
        currency, now, msg.message_id
    ))
    conn.commit()
    # —— 回复当笔明细 + 汇总 —— #
    # 计算单笔 usdt & commission
    after_fee = amt * (1 - fee/100)
    usdt = ceil2(after_fee / rate)
    comm_frac = commission/100
    comm_amt = ceil2(amt * comm_frac)
    # 序号
    cursor.execute('''
        SELECT COUNT(*) AS cnt
        FROM transactions
        WHERE chat_id=%s AND user_id=%s
    ''',(msg.chat.id, msg.from_user.id))
    seq = cursor.fetchone()['cnt']
    seq_str = str(seq).zfill(3)
    t = datetime.now().strftime('%H:%M:%S')
    text = (
        f"✅ 已入款 {amt} ({currency})\n"
        f"编号：{seq_str}\n"
        f"{seq}. {t} {amt}*"
        f"{(1-fee/100):.2f}/{rate} = {usdt}  {name}\n"
    )
    if commission>0:
        text += (
            f"{seq}. {t} {amt}*{comm_frac:.4f} = "
            f"{comm_amt} 【佣金】\n"
        )
    text += "\n" + show_summary(msg.chat.id, msg.from_user.id)
    bot.reply_to(msg, text)

# —— 启动轮询 —— #
bot.remove_webhook()
bot.infinity_polling()
