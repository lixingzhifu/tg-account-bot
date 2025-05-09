import os
import re
import math
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

# 初始化环境变量
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# 初始化 Bot 和数据库连接
bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 建表：settings 包含 chat_id 与 user_id 联合主键
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
# 建表：transactions 保留 message_id 作为唯一标识
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
)''')
conn.commit()

# 数字向上保留两位小数
def ceil2(x):
    return math.ceil(x * 100) / 100.0

# 获取当前用户配置
def get_settings(chat_id, user_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    r = cursor.fetchone()
    return (r['currency'], r['rate'], r['fee_rate'], r['commission_rate']) if r else ('RMB', 0, 0, 0)

# 汇总并生成统计
def show_summary(chat_id, user_id):
    cursor.execute(
        'SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total = sum(r['amount'] for r in rows)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    after_fee_total = ceil2(total * (1 - fee/100))
    usdt_total = ceil2(after_fee_total / rate) if rate else 0
    commission_rmb = ceil2(total * (commission/100))
    commission_usdt = ceil2(commission_rmb / rate) if rate else 0

    text = []
    for idx, r in enumerate(rows, start=1):
        t = r['date'].strftime('%H:%M:%S')
        net = ceil2(r['amount'] * (1 - r['fee_rate']/100) / r['rate']) if r['rate'] else 0
        text.append(f"{idx}. {t} {r['amount']}*{(1-r['fee_rate']/100):.2f}/{r['rate']} = {net} {r['name']}")
        if r['commission_rate'] > 0:
            c_amt = ceil2(r['amount']*r['commission_rate']/100)
            text.append(f"{idx}. {t} {r['amount']}*{r['commission_rate']/100:.2f} = {c_amt} 【佣金】")
    summary = '\n'.join(text)
    footer = (
        f"\n已入款（{len(rows)}笔）：{total}({currency})\n"
        f"汇率：{rate} | 费率：{fee}% | 佣金：{commission}%\n"
        f"应下发：{after_fee_total}({currency}) | {usdt_total}(USDT)\n"
    )
    if commission > 0:
        footer += f"中介佣金：{commission_rmb}({currency}) | {commission_usdt}(USDT)\n"
    return (summary + footer) if rows else "暂无交易记录。"

# /start 显示固定菜单
@bot.message_handler(commands=['start'])
def on_start(msg):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('🔁 计算重置', '📊 汇总')
    kb.row('❓ 需要帮助', '🛠️ 定制机器人')
    bot.send_message(msg.chat.id, "欢迎使用 LX 记账机器人 ✅\n请选择操作：", reply_markup=kb)

# /id 查看 chat_id 与 user_id
@bot.message_handler(commands=['id'])
def on_id(msg):
    bot.reply_to(msg, f"chat_id={msg.chat.id}\nuser_id={msg.from_user.id}")

# 显示交易设置指令模板
@bot.message_handler(func=lambda m: m.text in ['设置交易', '💱 设置交易'])
def show_template(m):
    tpl = (
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(m, tpl)

# 处理“设置交易指令”并存储
@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def set_trade(m):
    cid, uid = m.chat.id, m.from_user.id
    txt = m.text.replace('：',':')
    cur=rate=fee=com=None
    errs=[]
    for line in txt.split('\n'):
        key, _, val = line.partition(':')
        v = val.strip()
        if key.endswith('货币'):
            cur = re.sub('[^A-Za-z]','', v) or 'RMB'
        elif key.endswith('汇率'):
            try: rate = float(v)
            except: errs.append('汇率格式错误')
        elif key.endswith('费率'):
            try: fee = float(v)
            except: errs.append('费率格式错误')
        elif key.endswith('佣金'):
            try: com = float(v)
            except: errs.append('中介佣金请设置数字')
    if errs:
        bot.reply_to(m, '设置错误\n' + '\n'.join(errs))
        return
    if rate is None:
        bot.reply_to(m, '设置错误，缺少汇率')
        return
    try:
        cursor.execute(
            '''INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
               VALUES(%s,%s,%s,%s,%s,%s)
               ON CONFLICT(chat_id,user_id) DO UPDATE SET
                 currency=EXCLUDED.currency,
                 rate=EXCLUDED.rate,
                 fee_rate=EXCLUDED.fee_rate,
                 commission_rate=EXCLUDED.commission_rate''',
            (cid, uid, cur.upper(), rate, fee or 0, com or 0)
        )
        conn.commit()
        bot.reply_to(m,
            f"✅ 设置成功\n设置货币：{cur}\n设置汇率：{rate}\n设置费率：{fee or 0}%\n中介佣金：{com or 0}%"
        )
    except Exception as e:
        conn.rollback()
        bot.reply_to(m, f"设置失败：{e}")

# 加入交易记录：+1000 或 名称+1000
@bot.message_handler(func=lambda m: re.match(r'^([+加]\s*\d+)|(.+?[+加]\s*\d+)', m.text or ''))
def add_tx(m):
    cid, uid = m.chat.id, m.from_user.id
    t=m.text.strip()
    if t[0] in ['+','加']:
        name = m.from_user.first_name or ''
        amt = float(re.findall(r'\d+\.?\d*', t)[0])
    else:
        nm,amt = re.findall(r'(.+?)[+加]\s*(\d+\.?\d*)', t)[0]
        name, amt = nm.strip(), float(amt)
    cur,rate,fee,com = get_settings(cid,uid)
    now=datetime.now()
    cursor.execute(
        '''INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (cid,uid,name,amt,rate,fee,com,cur,now,m.message_id)
    )
    conn.commit()
    bot.reply_to(m, f"✅ 已入款 +{amt}({cur}) 编号:{m.message_id}\n" + show_summary(cid, uid))

# 启动长轮询
bot.remove_webhook()
bot.infinity_polling()
