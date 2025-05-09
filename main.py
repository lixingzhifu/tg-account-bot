import os
import re
import math
import telebot
import ps:contentReference[oaicite:9]{index=9}tCursor

# ---------- 配置区域 ----------
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
# ------------------------------

bot = telebot.TeleBot(TOKEN)

# 连接数据库
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 创建表（如果不存在）
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id        BIGINT    NOT NULL,
    user_id        BIGINT    NOT NULL,
    currency       TEXT      NOT NULL DEFAULT 'RMB',
    rate           DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee_rate       DOUBLE PRECISION NOT NULL DEFAULT 0,
    commission_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id              SERIAL    PRIMARY KEY,
    chat_id         BIGINT    NOT NULL,
    user_id         BIGINT    NOT NULL,
    name            TEXT      NOT NULL,
    amount          DOUBLE PRECISION NOT NULL,
    rate            DOUBLE PRECISION NOT NULL,
    fee_rate        DOUBLE PRECISION NOT NULL,
    commission_rate DOUBLE PRECISION NOT NULL,
    currency        TEXT      NOT NULL,
    date            TIMESTAMP NOT NULL DEFAULT NOW()
);
""")
conn.commit()

def ceil2(x):
    """向上保留两位小数"""
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    return "RMB", 0.0, 0.0, 0.0

def format_order_no(n):
    """把序号格式化为 3 位，不够前面补 0"""
    return f"{n:03d}"

def show_summary(chat_id, user_id):
    # 抓出所有记录
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    records = cursor.fetchall()

    total_amount = sum(r['amount'] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    # 计算“应下发”总值
    net_total_rmb = ceil2(total_amount * (1 - fee/100))
    net_total_usdt = ceil2(net_total_rmb / rate) if rate else 0

    # 计算中介佣金总额
    commission_rmb = ceil2(total_amount * commission/100)
    commission_usdt = ceil2(commission_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(records, start=1):
        t = r['date'].strftime("%d-%m-%Y %H:%M:%S")
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        no = format_order_no(idx)
        # 交易行
        lines.append(f"{no}. {t}  {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        # 如果有佣金
        if r['commission_rate'] > 0:
            com_amt = ceil2(r['amount'] * r['commission_rate']/100)
            lines.append(f"{no}. {t}  {r['amount']}*{r['commission_rate']/100:.2f} = {com_amt} 【佣金】")

    reply = "\n".join(lines) + "\n\n"
    reply += f"已入款（{len(records)}笔）：{total_amount} ({currency})\n"
    reply += f"已下发（0笔）：0.0 (USDT)\n\n"
    reply += f"总入款金额：{total_amount} ({currency})\n"
    reply += f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
    reply += f"应下发：{net_total_rmb}({currency}) | {net_total_usdt} (USDT)\n"
    reply += f"已下发：0.0({currency}) | 0.0 (USDT)\n"
    reply += f"未下发：{net_total_rmb}({currency}) | {net_total_usdt} (USDT)\n"
    if commission > 0:
        reply += f"\n中介佣金应下发：{commission_rmb}({currency}) | {commission_usdt} (USDT)"
    return reply

# ---- Bot Handlers ----

@bot.message_handler(commands=['start'])
def handle_start(msg):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("/trade", "/reset")
    kb.row("/summary", "/id")
    bot.send_message(msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n"
        "请选择：",
        reply_markup=kb
    )

@bot.message_handler(commands=['id'])
def handle_id(msg):
    bot.reply_to(msg, f"你的 chat_id={msg.chat.id}\n你的 user_id={msg.from_user.id}")

@bot.message_handler(commands=['reset'])
def handle_reset(msg):
    cursor.execute(
        "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
        (msg.chat.id, msg.from_user.id)
    )
    cursor.execute(
        "DELETE FROM settings WHERE chat_id=%s AND user_id=%s",
        (msg.chat.id, msg.from_user.id)
    )
    conn.commit()
    bot.reply_to(msg, "🔄 已清空所有该用户的配置与记录")

@bot.message_handler(commands=['trade'])
def handle_trade_sample(msg):
    bot.reply_to(msg,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith("设置交易指令"))
def handle_setting(msg):
    text = msg.text.replace('：',':').strip().splitlines()
    currency = rate = fee = commission = None
    errors = []
    for line in text:
        line = line.strip().replace(' ','')
        if line.startswith("设置货币:"):
            currency = line.split(":",1)[1].upper() or "RMB"
        elif line.startswith("设置汇率:"):
            try:
                rate = float(re.findall(r"\d+\.?\d*", line)[0])
            except:
                errors.append("汇率格式错误")
        elif line.startswith("设置费率:"):
            try:
                fee = float(re.findall(r"\d+\.?\d*", line)[0])
            except:
                errors.append("费率格式错误")
        elif line.startswith("中介佣金:"):
            try:
                commission = float(re.findall(r"\d+\.?\d*", line)[0])
            except:
                errors.append("中介佣金格式错误")
    if errors or rate is None:
        bot.reply_to(msg, "设置错误\n" + ("\n".join(errors) if errors else "缺少汇率"))
        return

    # 插入或更新
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (chat_id,user_id) DO UPDATE SET
                currency      = EXCLUDED.currency,
                rate          = EXCLUDED.rate,
                fee_rate      = EXCLUDED.fee_rate,
                commission_rate = EXCLUDED.commission_rate
        """, (
            msg.chat.id, msg.from_user.id,
            currency, rate, fee or 0.0, commission or 0.0
        ))
        conn.commit()
        bot.reply_to(msg,
            "✅ 设置成功\n"
            f"设置货币：{currency}\n"
            f"设置汇率：{rate}\n"
            f"设置费率：{fee or 0.0}%\n"
            f"中介佣金：{commission or 0.0}%"
        )
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"设置失败：{e}")

@bot.message_handler(func=lambda m: re.match(r'^[+-]\d+(\.\d+)?$', m.text.strip()))
def handle_amount(msg):
    sign, num = msg.text.strip()[0], msg.text.strip()[1:]
    try:
        amt = float(num)
    except:
        return

    # 记录交易
    name = msg.from_user.username or msg.from_user.first_name or "匿名"
    cid, uid = msg.chat.id, msg.from_user.id
    currency, rate, fee, commission = get_settings(cid, uid)
    now = datetime.now()
    cursor.execute("""
        INSERT INTO transactions(chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        cid, uid, name, amt, rate, fee, commission, currency, now
    ))
    conn.commit()

    # 取当前这一笔的序号
    cursor.execute("""
        SELECT count(*) AS cnt FROM transactions
        WHERE chat_id=%s AND user_id=%s
    """, (cid, uid))
    no = cursor.fetchone()['cnt']

    # 拼消息
    after_fee = amt * (1 - fee/100)
    usdt = ceil2(after_fee / rate) if rate else 0
    com_rmb = ceil2(amt * commission/100)
    com_usdt = ceil2(com_rmb / rate) if rate else 0

    reply = []
    reply.append(f"✅ 已入款 {sign}{amt:.2f} ({currency})")
    reply.append(f"编号：{format_order_no(no)}")
    tstr = now.strftime("%d-%m-%Y %H:%M:%S")
    reply.append(f"{tstr}  {amt}*{1-fee/100:.2f}/{rate} = {usdt}  @{name}")
    if commission>0:
        reply.append(f"{tstr}  {amt}*{commission/100:.2f} = {com_rmb} 【佣金】")
    reply.append("")  # 空行
    reply.append(show_summary(cid, uid))

    bot.reply_to(msg, "\n".join(reply))

@bot.message_handler(commands=['summary'])
def handle_summary(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    bot.reply_to(msg, show_summary(cid, uid))

bot.remove_webhook()
bot.infinity_polling()
