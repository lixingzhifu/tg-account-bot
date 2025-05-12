# main.py

import os
import re
from datetime import timedelta
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types

from utils import parse_trade_text, human_now, ceil2, parse_amount_text

# —— 环境变量 —— #
TOKEN        = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

bot = TeleBot(TOKEN)

# —— 数据库连接 & 建表 —— #
conn   = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id         BIGINT NOT NULL,
  user_id         BIGINT NOT NULL,
  currency        TEXT    NOT NULL,
  rate            DOUBLE PRECISION NOT NULL,
  fee_rate        DOUBLE PRECISION NOT NULL,
  commission_rate DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
  id              SERIAL PRIMARY KEY,
  chat_id         BIGINT NOT NULL,
  user_id         BIGINT NOT NULL,
  name            TEXT    NOT NULL,
  amount          DOUBLE PRECISION NOT NULL,
  rate            DOUBLE PRECISION NOT NULL,
  fee_rate        DOUBLE PRECISION NOT NULL,
  commission_rate DOUBLE PRECISION NOT NULL,
  currency        TEXT    NOT NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  message_id      BIGINT
);
""")
conn.commit()

# —— DB 操作 —— #
def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    return cursor.fetchone()  # None or dict

def upsert_settings(chat_id, user_id, currency, rate, fee, com):
    cursor.execute("""
    INSERT INTO settings
      (chat_id,user_id,currency,rate,fee_rate,commission_rate)
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT(chat_id,user_id) DO UPDATE SET
      currency = EXCLUDED.currency,
      rate     = EXCLUDED.rate,
      fee_rate = EXCLUDED.fee_rate,
      commission_rate = EXCLUDED.commission_rate
    """, (chat_id,user_id,currency,rate,fee,com))
    conn.commit()

def add_transaction(chat_id,user_id,name,amount,rate,fee,com,currency,dt,msg_id):
    cursor.execute("""
    INSERT INTO transactions
      (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,created_at,message_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    RETURNING id
    """, (chat_id,user_id,name,amount,rate,fee,com,currency,dt,msg_id))
    new_id = cursor.fetchone()["id"]
    conn.commit()
    return new_id

def delete_latest(chat_id,user_id):
    cursor.execute("""
    DELETE FROM transactions
    WHERE id = (
      SELECT id FROM transactions
      WHERE chat_id=%s AND user_id=%s
      ORDER BY id DESC LIMIT 1
    )
    RETURNING id
    """, (chat_id, user_id))
    row = cursor.fetchone()
    conn.commit()
    return row["id"] if row else None

def delete_by_id(chat_id,user_id,tid):
    cursor.execute("""
    DELETE FROM transactions
    WHERE chat_id=%s AND user_id=%s AND id=%s
    RETURNING id
    """, (chat_id, user_id, tid))
    row = cursor.fetchone()
    conn.commit()
    return row["id"] if row else None

def fetch_all(chat_id,user_id):
    cursor.execute("""
    SELECT * FROM transactions
    WHERE chat_id=%s AND user_id=%s
    ORDER BY id
    """, (chat_id, user_id))
    return cursor.fetchall()

# —— 权限判断 —— #
def is_admin(chat_id, user_id):
    info = bot.get_chat(chat_id)
    if info.type in ("group","supergroup"):
        admins = bot.get_chat_administrators(chat_id)
        return any(ad.user.id == user_id for ad in admins)
    return True

# —— /start & “记账” —— #
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text == '记账')
def cmd_start(msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💱 设置交易", "/trade")
    markup.row("🔁 清空记录", "/reset")
    markup.row("📊 汇总", "/summary")
    bot.send_message(msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=markup
    )

# —— /reset —— #
@bot.message_handler(commands=['reset'])
def cmd_reset(msg):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    cursor.execute(
      "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
      (msg.chat.id, msg.from_user.id)
    )
    conn.commit()
    bot.reply_to(msg, "🔄 已清空所有记录")

# —— /trade —— #
@bot.message_handler(commands=['trade'])
@bot.message_handler(func=lambda m: m.text == '💱 设置交易')
def cmd_trade(msg):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    bot.reply_to(msg,
      "格式如下（复制整段并修改）：\n"
      "设置交易指令\n"
      "设置货币：RMB\n"
      "设置汇率：9\n"
      "设置费率：2\n"
      "中介佣金：0.5"
    )

# —— 解析 & 存储 设置 —— #
@bot.message_handler(func=lambda m: m.text.startswith("设置交易指令"))
def cmd_set_trade(msg):
    if not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    cur, rate, fee, com, errs = parse_trade_text(msg.text)
    if errs:
        return bot.reply_to(msg, "设置错误：\n" + "\n".join(errs))
    upsert_settings(msg.chat.id, msg.from_user.id,
                    cur or "RMB", rate, fee or 0, com or 0)
    bot.reply_to(msg,
      "✅ 设置成功\n"
      f"设置货币：{cur or 'RMB'}\n"
      f"设置汇率：{rate}\n"
      f"设置费率：{fee or 0}%\n"
      f"设置佣金：{com or 0}%"
    )

# —— +1000 / 入1000 —— #
@bot.message_handler(func=lambda m: re.match(r'^[+]\s*\d+', m.text or ''))
@bot.message_handler(func=lambda m: re.match(r'^(入笔|入)\s*\d+', m.text or ''))
def cmd_transactions(msg):
    if msg.chat.type in ("group","supergroup") and not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    cfg = get_settings(msg.chat.id, msg.from_user.id)
    if not cfg:
        return bot.reply_to(msg, "❌ 请先「设置交易」并填写汇率，才能入笔。")

    _, amount = parse_amount_text(msg.text)
    if amount is None:
        return

    now_hms, now_dt = human_now()
    tid = add_transaction(
      msg.chat.id, msg.from_user.id,
      msg.from_user.username or msg.from_user.first_name or "匿名",
      amount, cfg[1], cfg[2], cfg[3], cfg[0], now_dt, msg.message_id
    )

    aft      = amount * (1 - cfg[2]/100)
    usdt     = ceil2(aft / cfg[1]) if cfg[1] else 0
    com_rmb  = ceil2(amount * (cfg[3]/100))
    com_usdt = ceil2(com_rmb / cfg[1]) if cfg[1] else 0

    s  = f"✅ 已入款 +{amount:.1f}\n"
    s += f"编号：{tid:03d}\n"
    s += f"1. {now_hms} {amount:.1f}*{1-cfg[2]/100:.2f}/{cfg[1]:.1f} = {usdt:.2f}  {msg.from_user.username}\n"
    if cfg[3] > 0:
        s += f"1. {now_hms} {amount:.1f}*{cfg[3]/100:.3f} = {com_rmb:.2f} 【佣金】\n"
    bot.reply_to(msg, s)

# —— 删除最近一笔 —— #
@bot.message_handler(func=lambda m: re.match(r'^-\s*\d+', m.text or ''))
def cmd_delete_latest(msg):
    if msg.chat.type in ("group","supergroup") and not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    tid = delete_latest(msg.chat.id, msg.from_user.id)
    if not tid:
        return bot.reply_to(msg, "⚠️ 无可删除记录")
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{tid:03d}")

# —— 删除指定编号 —— #
@bot.message_handler(func=lambda m: re.match(r'^删除订单\s*\d+', m.text or ''))
def cmd_delete_specific(msg):
    if msg.chat.type in ("group","supergroup") and not is_admin(msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    num = int(re.findall(r"\d+", msg.text)[0])
    tid = delete_by_id(msg.chat.id, msg.from_user.id, num)
    if not tid:
        return bot.reply_to(msg, f"⚠️ 找不到编号：{num:03d}")
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{num:03d}")

# —— /summary —— #
@bot.message_handler(commands=['summary'])
@bot.message_handler(func=lambda m: m.text == '📊 汇总')
def cmd_summary(msg):
    cfg = get_settings(msg.chat.id, msg.from_user.id)
    if not cfg:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能查看汇总。")
    rows = fetch_all(msg.chat.id, msg.from_user.id)
    total    = sum(r["amount"] for r in rows)
    usdt     = ceil2(total*(1-cfg[2]/100)/cfg[1]) if cfg[1] else 0
    com_rmb  = ceil2(total*(cfg[3]/100))
    com_usdt = ceil2(com_rmb/cfg[1]) if cfg[1] else 0

    lines = []
    for i, r in enumerate(rows,1):
        t   = (r["created_at"] + timedelta(hours=8)).strftime("%H:%M:%S")
        aft = r["amount"]*(1-r["fee_rate"]/100)
        u   = ceil2(aft/r["rate"]) if r["rate"] else 0
        lines.append(f"{i}. {t} {r['amount']:.1f}*{1-r['fee_rate']/100:.2f}/{r['rate']:.1f} = {u:.2f} {r['name']}")
        if r["commission_rate"]>0:
            cm = ceil2(r['amount']*r['commission_rate']/100)
            lines.append(f"{i}. {t} {r['amount']:.1f}*{r['commission_rate']/100:.3f} = {cm:.2f} 【佣金】")

    summary = "\n".join(lines) + (
        f"\n\n已入款（{len(rows)}笔）：{total:.1f} (RMB)\n"
        f"应下发：{ceil2(total*(1-cfg[2]/100)):.1f}(RMB) | {usdt:.2f}(USDT)\n"
        f"中介佣金：{com_rmb:.2f}(RMB) | {com_usdt:.2f}(USDT)"
    )
    bot.reply_to(msg, summary)

# —— 启动轮询 —— #
if __name__ == "__main__":
    bot.remove_webhook()      # 确保没有 webhook
    bot.infinity_polling()    # 永久轮询
