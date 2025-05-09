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

# --- 自动修复表结构 start ---

# 1) 先把旧数据里 settings.user_id 为 NULL 的记录用 chat_id 填充，避免后面 NOT NULL 约束出错
cursor.execute("""
    UPDATE settings
       SET user_id = chat_id
     WHERE user_id IS NULL
""")

# 2) transactions 表补齐三个字段（如果尚未存在）
cursor.execute("""
ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS user_id BIGINT,
  ADD COLUMN IF NOT EXISTS name TEXT,
  ADD COLUMN IF NOT EXISTS message_id BIGINT
""")

# 3) settings 表上重建复合主键 (chat_id, user_id)
cursor.execute("""
ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_pkey;
ALTER TABLE settings
  ADD CONSTRAINT settings_pkey PRIMARY KEY (chat_id, user_id)
""")

conn.commit()
# --- 自动修复表结构 end ---

# --- 如果表还不存在，就创建它们 ---
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
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
)
''')
conn.commit()

# 工具函数：向上取整到小数点后两位
def ceil2(n):
    return math.ceil(n * 100) / 100.0

# 读取当前设定
def get_settings(chat_id, user_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

# 汇总并格式化回复
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
    for i, row in enumerate(records, 1):
        t = datetime.strptime(row['date'], '%Y-%m-%d %H:%M:%S').strftime('%H:%M:%S')
        after_fee = row['amount'] * (1 - row['fee_rate']/100)
        usdt = ceil2(after_fee / row['rate']) if row['rate'] else 0
        line = f"{i}. {t} {row['amount']}*{(1-row['fee_rate']/100):.2f}/{row['rate']} = {usdt}  @{row['name']}"
        lines.append(line)
        if row['commission_rate'] > 0:
            comm_amt = ceil2(row['amount'] * row['commission_rate']/100)
            lines.append(f"{i}. {t} {row['amount']}*{row['commission_rate']/100:.2f} = {comm_amt} 【佣金】")

    reply  = "\n".join(lines) + "\n\n"
    reply += f"已入款（{len(records)}笔）：{total} ({currency})\n"
    reply += f"已下发（0笔）：0.0 (USDT)\n\n"
    reply += f"总入款金额：{total} ({currency})\n"
    reply += f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    reply += f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
    reply += f"已下发：0.0({currency}) | 0.0 (USDT)\n"
    reply += f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total} (USDT)\n"
    if commission>0:
        reply += f"\n中介佣金应下发：{commission_total_rmb}({currency}) | {commission_total_usdt} (USDT)"
    return reply

# /start 命令
@bot.message_handler(commands=['start'])
def handle_start(message):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('🔁 计算重启', '📊 汇总')
    kb.row('❓ 需要帮助', '🛠️ 定制机器人')
    bot.send_message(
        message.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
        reply_markup=kb
    )

# /id 命令：查看 chat_id 和 user_id
@bot.message_handler(commands=['id'])
def handle_id(message):
    bot.reply_to(message,
        f"你的 chat_id 是：{message.chat.id}\n你的 user_id 是：{message.from_user.id}"
    )

# 点击「设置交易」
@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def handle_set(message):
    bot.reply_to(message,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

# 真正解析并保存「设置交易指令」
@bot.message_handler(func=lambda m: m.text and '设置交易指令' in m.text)
def set_trade_config(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    text = message.text.replace('：',':')

    currency = rate = fee = commission = None
    errors = []

    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('设置货币'):
            cur = line.split(':',1)[1].strip()
            currency = cur or 'RMB'
        elif line.startswith('设置汇率'):
            try:
                rate = float(re.findall(r'\d+\.?\d*', line)[0])
            except:
                errors.append("汇率格式错误")
        elif line.startswith('设置费率'):
            try:
                fee = float(re.findall(r'\d+\.?\d*', line)[0])
            except:
                errors.append("费率格式错误")
        elif line.startswith('中介佣金'):
            try:
                commission = float(re.findall(r'\d+\.?\d*', line)[0])
            except:
                errors.append("中介佣金请设置数字")

    if errors:
        bot.reply_to(message, "设置错误\n" + "\n".join(errors))
        return
    if rate is None:
        bot.reply_to(message, "设置错误，至少需要提供汇率：设置汇率：9")
        return

    # 写入数据库
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              currency=EXCLUDED.currency,
              rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate,
              commission_rate=EXCLUDED.commission_rate
        """, (chat_id, user_id, currency, rate, fee or 0, commission or 0))
        conn.commit()
        bot.reply_to(message,
            f"✅ 设置成功\n"
            f"设置货币：{currency}\n"
            f"设置汇率：{rate}\n"
            f"设置费率：{fee or 0}%\n"
            f"中介佣金：{commission or 0}%"
        )
    except Exception as e:
        conn.rollback()
        bot.reply_to(message, f"设置失败，请检查格式或联系管理员\n错误信息：{e}")

# 入笔 / 加款
@bot.message_handler(func=lambda m: re.match(r'^([+加])\s*\d+(\.\d+)?', m.text))
def handle_amount(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    txt = message.text.strip()

    # 提取金额和昵称
    m = re.match(r'^([+加])\s*(\d+(\.\d+)?)$', txt)
    if m:
        name = message.from_user.username or message.from_user.first_name or "匿名"
        amount = float(m.group(2))
    else:
        # 支持 '@ABC +1000' 形式
        parts = re.findall(r'(.+?)\s*[+加]\s*(\d+(\.\d+)?)', txt)
        if not parts:
            return
        name, amt, _ = parts[0]
        name = name.strip()
        amount = float(amt)

    # 读取设定
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # 写入 transactions
    cursor.execute("""
        INSERT INTO transactions
          (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat_id,user_id,name,amount,rate,fee,commission,currency,now,message.message_id))
    conn.commit()

    # 回复入笔并附上汇总
    bot.reply_to(message,
        f"✅ 已入款 +{amount} ({currency})\n"
        f"编号：{message.message_id}\n\n"
        + show_summary(chat_id, user_id)
    )

bot.remove_webhook()
bot.infinity_polling()
