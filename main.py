import os
import re
import math
import psycopg2
from psycopg2.extras import RealDictCursor
import telebot
from telebot import types
from datetime import datetime, timedelta

# ─── 环境变量 ───────────────────────────────────────────────────────────────
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = telebot.TeleBot(TOKEN)

# ─── 数据库连接 & 建表 ─────────────────────────────────────────────────────────
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# settings 表：每个 (chat_id, user_id) 一条配置
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id         BIGINT,
    user_id         BIGINT,
    currency        TEXT    DEFAULT 'RMB',
    rate            DOUBLE PRECISION DEFAULT 0,
    fee_rate        DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
""")

# transactions 表：流水记录
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id               SERIAL PRIMARY KEY,
    chat_id          BIGINT,
    user_id          BIGINT,
    name             TEXT,
    amount           DOUBLE PRECISION,
    rate             DOUBLE PRECISION,
    fee_rate         DOUBLE PRECISION,
    commission_rate  DOUBLE PRECISION,
    currency         TEXT,
    created_at       TIMESTAMP,
    message_id       BIGINT
);
""")
conn.commit()

# ─── 辅助函数 ────────────────────────────────────────────────────────────────
def is_admin(chat_id, user_id):
    """群组中判断是否管理员；私聊或频道一律 True"""
    ct = bot.get_chat(chat_id).type
    if ct in ("group", "supergroup"):
        try:
            admins = bot.get_chat_administrators(chat_id)
            return any(ad.user.id == user_id for ad in admins)
        except:
            return False
    return True

def get_settings(chat_id, user_id):
    cursor.execute("""
        SELECT currency, rate, fee_rate, commission_rate
          FROM settings
         WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    row = cursor.fetchone()
    return row or None

def human_now():
    # Malaysia time = UTC+8
    dt = datetime.utcnow() + timedelta(hours=8)
    return dt.strftime("%H:%M:%S"), dt

def ceil2(x):
    return math.ceil(x*100)/100.0

# ─── /start & 菜单 ───────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💱 设置交易", "/trade")
    markup.row("🔁 清空记录", "/reset")
    markup.row("📊 汇总", "/summary")
    bot.send_message(
        msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=markup
    )

# ─── 清空当前用户所有流水 ──────────────────────────────────────────────────────
@bot.message_handler(commands=["reset"])
def cmd_reset(msg):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    cursor.execute("""
        DELETE FROM transactions
         WHERE chat_id=%s AND user_id=%s
    """, (msg.chat.id, msg.from_user.id))
    conn.commit()
    bot.reply_to(msg, "🔄 已清空本群组本用户的所有记录")

# ─── /trade 或“设置交易”──发送示例模板───────────────────────────────────────
@bot.message_handler(commands=["trade"])
@bot.message_handler(func=lambda m: m.text in ("💱 设置交易", "设置交易"))
def cmd_trade(msg):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    bot.reply_to(msg,
        "格式如下（请复制整段并修改数字/货币字母）：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：9\n"
        "设置费率：2\n"
        "中介佣金：0.5"
    )

# ─── 解析并存储配置 ─────────────────────────────────────────────────────────
@bot.message_handler(func=lambda m: m.text and "设置交易指令" in m.text)
def set_trade_config(msg):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    text = msg.text.replace("：",":").strip()
    # 默认值
    currency = None
    rate = fee = commission = None
    errors = []

    for line in text.splitlines():
        line = line.strip().replace(" ", "")
        if line.startswith("设置货币:"):
            currency = re.sub(r"[^A-Za-z]", "", line.split(":",1)[1]).upper()
        elif line.startswith("设置汇率:"):
            val = line.split(":",1)[1]
            try:
                rate = float(val)
            except:
                errors.append("汇率格式错误")
        elif line.startswith("设置费率:"):
            val = line.split(":",1)[1]
            try:
                fee = float(val)
            except:
                errors.append("费率格式错误")
        elif line.startswith("中介佣金:"):
            val = line.split(":",1)[1]
            try:
                commission = float(val)
            except:
                errors.append("佣金格式错误")

    if errors:
        return bot.reply_to(msg, "设置错误\n" + "\n".join(errors))

    if rate is None:
        return bot.reply_to(msg, "❌ 至少需要提供汇率：设置汇率：9")

    # 写入数据库（有则更新，无则插入）
    cursor.execute("""
        INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT (chat_id,user_id)
        DO UPDATE SET
            currency = EXCLUDED.currency,
            rate     = EXCLUDED.rate,
            fee_rate = EXCLUDED.fee_rate,
            commission_rate = EXCLUDED.commission_rate
    """, (
        msg.chat.id, msg.from_user.id,
        currency or "RMB", rate, fee or 0, commission or 0
    ))
    conn.commit()

    bot.reply_to(msg,
        "✅ 设置成功\n"
        f"设置货币：{currency or 'RMB'}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee or 0}%\n"
        f"中介佣金：{commission or 0}%"
    )

# ─── 新增流水 / 删除最近一笔 / 删除指定流水 ────────────────────────────────────
@bot.message_handler(func=lambda m: bool(re.match(r"^(\+|加)\s*\d", m.text)) or bool(re.match(r"^.+(\+|加)\s*\d", m.text)))
def handle_amount(msg):
    # 1) 权限检查
    if msg.chat.type in ("group","supergroup") and not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    # 2) 配置检查
    cfg = get_settings(msg.chat.id, msg.from_user.id)
    if not cfg:
        return bot.reply_to(msg, "❌ 请先发送“设置交易”并填写汇率，才能入笔。")

    currency, rate, fee_rate, commission_rate = cfg
    txt = msg.text.strip()
    # 匹配 +1000 或 名称+1000
    m = re.match(r"^(?:\+|加)\s*(\d+\.?\d*)$", txt)
    if m:
        name = msg.from_user.first_name or "匿名"
        amount = float(m.group(1))
    else:
        # 名称 + 数字
        parts = re.findall(r"(.+?)(?:\+|加)\s*(\d+\.?\d*)", txt)
        if not parts:
            return
        name = parts[0][0].strip()
        amount = float(parts[0][1])

    # 插入一条
    now_hms, now_dt = human_now()
    cursor.execute("""
        INSERT INTO transactions
            (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,created_at,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (
        msg.chat.id, msg.from_user.id,
        name, amount,
        rate, fee_rate, commission_rate,
        currency, now_dt, msg.message_id
    ))
    new_id = cursor.fetchone()["id"]
    conn.commit()

    # 计算并回复当笔详情
    after_fee = amount*(1 - fee_rate/100)
    usdt = ceil2(after_fee / rate) if rate else 0
    com_amt_rmb = ceil2(amount * (commission_rate/100))
    com_amt_usdt = ceil2(com_amt_rmb / rate) if rate else 0

    reply = (
        f"✅ 已入款 +{amount:.1f} ({currency})\n"
        f"编号：{new_id:03d}\n"
        f"1. {now_hms} {amount:.1f}*{1 - fee_rate/100:.2f}/{rate:.1f} = {usdt:.2f} {name}\n"
    )
    if commission_rate>0:
        reply += f"1. {now_hms} {amount:.1f}*{commission_rate/100:.3f} = {com_amt_rmb:.2f} 【佣金】\n"

    return bot.reply_to(msg, reply)

@bot.message_handler(func=lambda m: m.text.strip().startswith("-"))
def delete_latest(msg):
    # 只支持管理员
    if msg.chat.type in ("group","supergroup") and not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    # 找到最新一笔
    cursor.execute("""
        SELECT id FROM transactions
         WHERE chat_id=%s AND user_id=%s
         ORDER BY created_at DESC
         LIMIT 1
    """, (msg.chat.id, msg.from_user.id))
    row = cursor.fetchone()
    if not row:
        return bot.reply_to(msg, "⚠️ 无可删除的记录")
    tid = row["id"]
    cursor.execute("DELETE FROM transactions WHERE id=%s", (tid,))
    conn.commit()
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{tid:03d}")

@bot.message_handler(func=lambda m: bool(re.match(r"^删除订单\s*\d+", m.text)))
def delete_specific(msg):
    if msg.chat.type in ("group","supergroup") and not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    num = int(re.findall(r"\d+", msg.text)[0])
    cursor.execute("""
        DELETE FROM transactions
         WHERE chat_id=%s AND user_id=%s AND id=%s
        RETURNING id
    """, (msg.chat.id, msg.from_user.id, num))
    row = cursor.fetchone()
    if not row:
        return bot.reply_to(msg, f"⚠️ 找不到编号：{num:03d}")
    conn.commit()
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{num:03d}")

# ─── 汇总 /summary ────────────────────────────────────────────────────────────
@bot.message_handler(commands=["summary"])
@bot.message_handler(func=lambda m: m.text=="📊 汇总")
def cmd_summary(msg):
    cfg = get_settings(msg.chat.id, msg.from_user.id)
    if not cfg:
        return bot.reply_to(msg, "❌ 请先发送“设置交易”并填写汇率，才能查看汇总。")
    currency, rate, fee_rate, commission_rate = cfg

    # 全部流水
    cursor.execute("""
        SELECT * FROM transactions
         WHERE chat_id=%s AND user_id=%s
         ORDER BY created_at
    """, (msg.chat.id, msg.from_user.id))
    rows = cursor.fetchall()

    total_in = sum(r["amount"] for r in rows)
    total_usdt = ceil2(total_in*(1-fee_rate/100)/rate) if rate else 0
    total_com_rmb = ceil2(total_in*commission_rate/100)
    total_com_usdt = ceil2(total_com_rmb/rate) if rate else 0

    lines = []
    for i,r in enumerate(rows,1):
        t = (r["created_at"] + timedelta(hours=8)).strftime("%H:%M:%S")
        aft = r["amount"]*(1-r["fee_rate"]/100)
        usdt = ceil2(aft / r["rate"]) if r["rate"] else 0
        lines.append(f"{i}. {t} {r['amount']:.1f}*{1-r['fee_rate']/100:.2f}/{r['rate']:.1f} = {usdt:.2f} {r['name']}")
        if r["commission_rate"]>0:
            com = ceil2(r["amount"]*r["commission_rate"]/100)
            lines.append(f"{i}. {t} {r['amount']:.1f}*{r['commission_rate']/100:.3f} = {com:.2f} 【佣金】")

    summary = "\n".join(lines)
    summary += (
        f"\n\n已入款（{len(rows)}笔）：{total_in:.1f} ({currency})\n"
        f"已下发（0笔）：0 (USDT)\n\n"
        f"总入款金额：{total_in:.1f} ({currency})\n"
        f"汇率：{rate:.1f}\n"
        f"费率：{fee_rate:.1f}%\n"
        f"佣金：{commission_rate:.1f}%\n\n"
        f"应下发：{ceil2(total_in*(1-fee_rate/100)):.1f}({currency}) | {total_usdt:.2f} (USDT)\n"
        f"已下发：0.0 ({currency}) | 0.00 (USDT)\n"
        f"未下发：{ceil2(total_in*(1-fee_rate/100)):.1f}({currency}) | {total_usdt:.2f} (USDT)\n"
    )
    if commission_rate>0:
        summary += (
            f"\n中介佣金应下发：{total_com_rmb:.2f}({currency}) | {total_com_usdt:.2f} (USDT)"
        )

    bot.reply_to(msg, summary)

# ─── 启动轮询 ───────────────────────────────────────────────────────────────
bot.remove_webhook()
bot.infinity_polling()
