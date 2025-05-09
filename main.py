```python
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
# 固定命令菜单
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
def ceil2(x):
    return math.ceil(x * 100) / 100.0

# 获取设置
def get_settings(chat_id, user_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

# 生成汇总
def build_summary(chat_id, user_id):
    cursor.execute(
        'SELECT name, amount, rate, fee_rate, commission_rate, date FROM transactions WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total_amt = sum(r['amount'] for r in rows)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    converted = ceil2(total_amt * (1 - fee/100) / rate) if rate else 0
    comm_rmb = ceil2(total_amt * commission/100)
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    lines = []
    for r in rows:
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        line = f"{t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}"
        if r['commission_rate']>0:
            com_amt = ceil2(r['amount'] * r['commission_rate']/100)
            line += f"\n{t} {r['amount']}*{r['commission_rate']/100} = {com_amt} 【佣金】"
        lines.append(line)
    today = datetime.now().strftime('%d-%m-%Y')
    header = f"订单号：{today}"
    footer = (
        f"\n这里是今天的总数\n"
        f"已入款（{len(rows)}笔）：{total_amt} ({currency})\n"
        f"已下发（0笔）：0 ({currency})\n\n"
        f"总入款金额：{total_amt} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{ceil2(total_amt*(1-fee/100))}({currency}) | {converted} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total_amt*(1-fee/100))}({currency}) | {converted} (USDT)\n"
    )
    if commission>0:
        footer += f"\n中介佣金应下发：{comm_usdt} (USDT)"
    return header + '\n'.join([''] + lines) + '\n' + footer

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
    lines = msg.text.split('\n')[1:]
    params = {'currency':None,'rate':None,'fee_rate':None,'commission_rate':None}
    for l in lines:
        if ':' in l:
            k,v = l.split(':',1); v=v.strip()
            try:
                if '设置货币' in k: params['currency']=v
                elif '设置汇率' in k: params['rate']=float(v)
                elif '设置费率' in k: params['fee_rate']=float(v)
                elif '中介佣金' in k: params['commission_rate']=float(v)
            except:
                return bot.reply_to(msg, f"设置失败\n{k}格式请设置数字")
    if params['rate'] is None:
        return bot.reply_to(msg, '设置失败\n至少需要提供汇率：设置汇率：9')
    # upsert
    cursor.execute('SELECT 1 FROM settings WHERE chat_id=%s AND user_id=%s',(chat_id,user_id))
    if cursor.fetchone():
        cursor.execute(
            'UPDATE settings SET currency=%s,rate=%s,fee_rate=%s,commission_rate=%s WHERE chat_id=%s AND user_id=%s',
            (params['currency'],params['rate'],params['fee_rate'],params['commission_rate'],chat_id,user_id)
        )
    else:
        cursor.execute(
            'INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate) VALUES(%s,%s,%s,%s,%s,%s)',
            (chat_id,user_id,params['currency'],params['rate'],params['fee_rate'],params['commission_rate'])
        )
    conn.commit()
    bot.reply_to(msg,
        f"✅ 设置成功\n"
        f"设置货币：{params['currency']}\n"
        f"设置汇率：{params['rate']}\n"
        f"设置费率：{params['fee_rate']}\n"
        f"中介佣金：{params['commission_rate']}"
    )

@bot.message_handler(func=lambda m: re.match(r'^\+\d+(\.\d+)?$',m.text.strip()))
def deposit(msg):
    chat_id,user_id = msg.chat.id,msg.from_user.id
    amt=float(msg.text.lstrip('+'))
    name=msg.from_user.username or msg.from_user.first_name or '匿名'
    c,r,f,cm=get_settings(chat_id,user_id)
    now=datetime.now()
    cursor.execute(
        'INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (chat_id,user_id,name,amt,r,f,cm,c,now)
    )
    conn.commit()
    summary = build_summary(chat_id,user_id)
    bot.reply_to(msg, f"✅ 已入款 +{amt} ({c})\n日期\n{summary}")

if __name__=='__main__':
    bot.infinity_polling(timeout=60)
```
