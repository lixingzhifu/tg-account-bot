import os
import re
import pytz
from datetime import datetime, timedelta
from telebot import TeleBot, types
import psycopg2
from psycopg2.extras import RealDictCursor

# —— 环境变量 —— #
TOKEN        = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# —— Bot 实例 —— #
bot = TeleBot(TOKEN)

# —— 数据库连接 —— #
conn   = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 初始化建表 —— #
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
  id               SERIAL PRIMARY KEY,
  chat_id          BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  name             TEXT    NOT NULL,
  amount           DOUBLE PRECISION NOT NULL,
  rate             DOUBLE PRECISION NOT NULL,
  fee_rate         DOUBLE PRECISION NOT NULL,
  commission_rate  DOUBLE PRECISION NOT NULL,
  currency         TEXT    NOT NULL,
  date             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  message_id       BIGINT,
  deducted_amount  DOUBLE PRECISION DEFAULT 0.0
);
""")
conn.commit()

# —— /start & “设置交易” —— #
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('/trade', '/commands', '/reset', '/show')
    bot.reply_to(msg,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=kb
    )

@bot.message_handler(commands=['trade'])
def cmd_trade(msg):
    bot.reply_to(msg,
        "请按格式发送：\n\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0.0"
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith("设置交易指令"))
def handle_trade(msg):
    text = msg.text.replace('：',':')
    try:
        cur  = re.search(r'设置货币:([^\s]+)', text).group(1)
        rate = float(re.search(r'设置汇率:([\d.]+)', text).group(1))
        fee  = float(re.search(r'设置费率:([\d.]+)', text).group(1))
        comm = float(re.search(r'中介佣金:([\d.]+)', text).group(1))
    except:
        return bot.reply_to(msg, "❌ 格式错误，示例：\n设置交易指令\n设置货币：RMB\n设置汇率：9\n设置费率：2\n中介佣金：0.5")

    cid = msg.chat.id; uid = msg.from_user.id
    try:
        cursor.execute("""
          INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
          VALUES(%s,%s,%s,%s,%s,%s)
          ON CONFLICT(chat_id,user_id) DO UPDATE
           SET currency=EXCLUDED.currency, rate=EXCLUDED.rate,
               fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate
        """, (cid,uid,cur,rate,fee,comm))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    bot.reply_to(msg, f"✅ 设置成功\n货币：{cur}\n汇率：{rate}\n费率：{fee}%\n佣金率：{comm}%")

# —— 入账（记录交易并显示今日摘要） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]+\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    cid, uid = msg.chat.id, msg.from_user.id

    # 1) 取配置
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s", (cid,uid))
    s = cursor.fetchone()
    if not s:
        return bot.reply_to(msg, "❌ 请先 /trade 设置交易参数。")

    # 2) 解析金额
    amt = float(re.findall(r'[\+入笔]+([\d.]+)', msg.text)[0])
    after_fee = amt * (1 - s['fee_rate']/100)

    # 3) 入库
    try:
        cursor.execute("""
          INSERT INTO transactions
           (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,
            currency,message_id,deducted_amount)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            cid, uid, msg.from_user.username,
            amt, s['rate'], s['fee_rate'], s['commission_rate'],
            s['currency'], msg.message_id, after_fee
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 4) 汇总全量
    cursor.execute("SELECT COUNT(*) AS cnt FROM transactions WHERE chat_id=%s AND user_id=%s", (cid,uid))
    cnt = cursor.fetchone()['cnt']
    cursor.execute("""
      SELECT SUM(amount) AS sum_amt, SUM(deducted_amount) AS sum_pending
      FROM transactions WHERE chat_id=%s AND user_id=%s
    """, (cid,uid))
    agg = cursor.fetchone()
    total_amt     = float(agg['sum_amt']     or 0)
    total_pending = float(agg['sum_pending'] or 0)
    total_issued  = 0.0
    total_unissued= total_pending
    tp_usdt = round(total_pending  / s['rate'],2)
    ti_usdt = round(total_issued   / s['rate'],2)
    tu_usdt = round(total_unissued / s['rate'],2)

    # 5) 抽取“今日入笔”
    cursor.execute("""
      SELECT id, date, amount, fee_rate, rate, name, commission_rate
      FROM transactions
      WHERE chat_id=%s AND user_id=%s
        AND (date + INTERVAL '8 hours')::date = (NOW() + INTERVAL '8 hours')::date
      ORDER BY date
    """, (cid,uid))
    rows = cursor.fetchall()

    lines = []; pc = 0; comm_rmb = 0.0
    for r in rows:
        sign = '+' if r['amount']>0 else '-'
        a = abs(r['amount'])
        af = a*(1 - r['fee_rate']/100)
        u  = round(af/r['rate'],2)
        ts = (r['date'] + timedelta(hours=8)).strftime('%H:%M:%S')
        lines.append(f"{r['id']:03d}. {ts}  {sign}{a} * {1-r['fee_rate']/100} / {r['rate']} = {u}  {r['name']}")
        if r['amount']>0: pc +=1
        comm_rmb += a*(r['commission_rate']/100)

    # 6) 构造回复
    text  = f"今日入笔（{pc}笔）\n"
    text += ("\n".join(lines)+"\n\n") if lines else "\n\n"
    text += "今日下发（0笔）\n\n"
    text += (
        f"已入款（{cnt}笔）：{total_amt} ({s['currency']})\n\n"
        f"应下发：{total_pending} ({s['currency']}) | {tp_usdt} (USDT)\n"
        f"已下发：{total_issued} ({s['currency']}) | {ti_usdt} (USDT)\n"
        f"未下发：{total_unissued} ({s['currency']}) | {tu_usdt} (USDT)\n\n"
        f"佣金应下发：{round(comm_rmb,2)} ({s['currency']}) | {round(comm_rmb/s['rate'],2)} (USDT)\n"
        f"佣金已下发：0.0 ({s['currency']}) | 0.00 (USDT)\n"
        f"佣金未下发：{round(comm_rmb,2)} ({s['currency']}) | {round(comm_rmb/s['rate'],2)} (USDT)\n"
    )
    bot.reply_to(msg, text)

# —— 重置清零 —— #
@bot.message_handler(commands=['reset'])
def cmd_reset(msg):
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s", (msg.chat.id, msg.from_user.id))
    conn.commit()
    bot.reply_to(msg, "✅ 记录已清零！")

# —— 启动轮询 —— #
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling()
