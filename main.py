import os
import re
import math
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

# 从环境变量读取
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = telebot.TeleBot(TOKEN)

# 初始化数据库连接
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 创建 settings 表（含 chat_id+user_id 联合主键）
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    currency TEXT    DEFAULT 'RMB',
    rate DOUBLE PRECISION      DEFAULT 0,
    fee_rate DOUBLE PRECISION  DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
""")

# 创建 transactions 表
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    name TEXT,
    amount DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    fee_rate DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date TIMESTAMP,
    message_id BIGINT
)
""")

conn.commit()


def ceil2(x: float) -> float:
    """向上保留两位小数"""
    return math.ceil(x * 100) / 100.0


def get_settings(chat_id: int, user_id: int):
    """读取当前设置，若无则返回默认"""
    cursor.execute("""
        SELECT currency, rate, fee_rate, commission_rate
          FROM settings
         WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    row = cursor.fetchone()
    if row:
        return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]
    else:
        return "RMB", 0.0, 0.0, 0.0


def show_summary(chat_id: int, user_id: int) -> str:
    """拼接汇总信息"""
    cursor.execute("""
        SELECT * FROM transactions
         WHERE chat_id=%s AND user_id=%s
         ORDER BY id
    """, (chat_id, user_id))
    records = cursor.fetchall()

    total_amount = sum(r["amount"] for r in records)
    currency, rate, fee, cm = get_settings(chat_id, user_id)
    after_fee_total = total_amount * (1 - fee/100)
    usdt_total = ceil2(after_fee_total / rate) if rate else 0

    text = []
    # 列出每笔
    for idx, r in enumerate(records, start=1):
        t = r["date"].strftime("%H:%M:%S")
        amt = r["amount"]
        after_fee = amt * (1 - r["fee_rate"]/100)
        usdt = ceil2(after_fee / r["rate"]) if r["rate"] else 0
        line = f"{idx}. {t}  {amt}*{(1 - r['fee_rate']/100):.2f}/{r['rate']} = {usdt}  {r['name']}"
        text.append(line)
        if r["commission_rate"] > 0:
            cm_amt = ceil2(amt * r["commission_rate"]/100)
            text.append(f"{idx}. {t}  {amt}*{r['commission_rate']/100:.2f} = {cm_amt} 【佣金】")

    # 汇总
    text.append("")
    text.append(f"已入款（{len(records)}笔）：{total_amount} ({currency})")
    text.append(f"总入款金额：{total_amount} ({currency})")
    text.append(f"汇率：{rate}")
    text.append(f"费率：{fee}%")
    text.append(f"佣金：{cm}%")
    text.append("")
    text.append(f"应下发：{ceil2(total_amount*(1-fee/100))}({currency}) | {usdt_total} (USDT)")
    text.append(f"已下发：0.0({currency}) | 0.0 (USDT)")
    text.append(f"未下发：{ceil2(total_amount*(1-fee/100))}({currency}) | {usdt_total} (USDT)")

    if cm > 0:
        cm_rmb = ceil2(total_amount * cm/100)
        cm_usdt = ceil2(cm_rmb / rate) if rate else 0
        text.append(f"中介佣金应下发：{cm_rmb}({currency}) | {cm_usdt} (USDT)")

    return "\n".join(text)


@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('🔁 计算重启', '📊 汇总')
    kb.row('❓ 需要帮助', '🛠️ 定制机器人')
    bot.send_message(msg.chat.id,
                     "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
                     reply_markup=kb)


@bot.message_handler(commands=['id'])
def cmd_id(msg):
    bot.reply_to(msg,
                 f"你的 chat_id：{msg.chat.id}\n你的 user_id：{msg.from_user.id}")


@bot.message_handler(func=lambda m: m.text in ['设置交易', '💱 设置交易'])
def cmd_show_template(msg):
    bot.reply_to(msg,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )


@bot.message_handler(func=lambda m: m.text and '设置交易指令' in m.text)
def set_trade_config(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    text = msg.text.replace('：', ':')
    currency = None
    rate = fee = cm = None
    errors = []

    for line in text.splitlines():
        line = line.strip().replace(' ', '')
        if line.startswith('设置货币'):
            v = line.split(':',1)[1]
            currency = re.sub(r'[^A-Za-z]', '', v).upper()
        if line.startswith('设置汇率'):
            try:
                rate = float(re.findall(r'\d+\.?\d*', line)[0])
            except:
                errors.append("汇率格式错误")
        if line.startswith('设置费率'):
            try:
                fee = float(re.findall(r'\d+\.?\d*', line)[0])
            except:
                errors.append("费率格式错误")
        if line.startswith('中介佣金'):
            try:
                cm = float(re.findall(r'\d+\.?\d*', line)[0])
            except:
                errors.append("中介佣金格式错误")

    if errors:
        bot.reply_to(msg, "设置错误\n" + "\n".join(errors))
        return
    if rate is None:
        bot.reply_to(msg, "设置错误，缺少汇率")
        return

    # 写入数据库
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE
               SET currency = EXCLUDED.currency,
                   rate = EXCLUDED.rate,
                   fee_rate = EXCLUDED.fee_rate,
                   commission_rate = EXCLUDED.commission_rate
        """, (chat_id, user_id,
              currency or 'RMB', rate,
              fee or 0.0, cm or 0.0))
        conn.commit()
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"设置失败，请联系管理员\n错误信息：{e}")
        return

    bot.reply_to(msg,
        f"✅ 设置成功\n"
        f"设置货币：{currency or 'RMB'}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee or 0.0}%\n"
        f"中介佣金：{cm or 0.0}%"
    )


@bot.message_handler(func=lambda m: re.match(r'^[+加].*\d+', m.text or ''))
def handle_amount(msg):
    chat_id = msg.chat.id
    user_id = msg.from_user.id
    txt = msg.text.strip()

    # 提取金额和备注
    m = re.match(r'^[+加]\s*(\d+\.?\d*)$', txt)
    if m:
        name = msg.from_user.username or msg.from_user.first_name or ''
        amount = float(m.group(1))
    else:
        parts = re.findall(r'(.+?)[+加]\s*(\d+\.?\d*)', txt)
        if not parts:
            return
        name, amt = parts[0]
        name = name.strip()
        amount = float(amt)

    currency, rate, fee, cm = get_settings(chat_id, user_id)
    now = datetime.now()

    try:
        cursor.execute("""
            INSERT INTO transactions
              (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, date, message_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, name, amount, rate, fee, cm, currency, now, msg.message_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"记账失败\n{e}")
        return

    # 取刚才的记录编号
    trans_id = cursor.lastrowid if hasattr(cursor, 'lastrowid') else msg.message_id

    reply = [f"✅ 已入款 +{amount} ({currency})",
             f"编号：{str(trans_id).zfill(3)}"]
    # 明细
    after_fee = amount * (1 - fee/100)
    usdt = ceil2(after_fee / rate) if rate else 0
    reply.append(f"{now.strftime('%d-%m-%Y')} {now.strftime('%H:%M:%S')} "
                 f"{amount}*{(1-fee/100):.2f}/{rate} = {usdt}  {name}")
    if cm > 0:
        cm_amt = ceil2(amount * cm/100)
        reply.append(f"{now.strftime('%d-%m-%Y')} {now.strftime('%H:%M:%S')} "
                     f"{amount}*{cm/100:.2f} = {cm_amt} 【佣金】")

    # 汇总
    reply.append("")
    reply.append(show_summary(chat_id, user_id))

    bot.reply_to(msg, "\n".join(reply))


# 启动轮询
bot.remove_webhook()
bot.infinity_polling()
