import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
from datetime import datetime
import pytz

# —— 环境变量 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# —— Bot 实例 & 数据库连接 —— #
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 初始化建表 —— #
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id         BIGINT NOT NULL,
  user_id         BIGINT NOT NULL,
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
  amount           DOUBLE PRECISION NOT NULL,
  rate             DOUBLE PRECISION NOT NULL,
  fee_rate         DOUBLE PRECISION NOT NULL,
  commission_rate  DOUBLE PRECISION NOT NULL,
  date             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  message_id       BIGINT,
  status           TEXT DEFAULT 'pending', -- status to mark if transaction is deleted
  deducted_amount  DOUBLE PRECISION DEFAULT 0.0
);
""")
conn.commit()

# —— /start & 记账 —— #
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton('/trade'), types.KeyboardButton('记账'))
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择菜单：", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == '记账')
def alias_start(msg):
    cmd_start(msg)

# —— 设置交易 —— #
@bot.message_handler(func=lambda m: re.match(r'^(/trade|设置交易)', m.text or ''))
def cmd_set_trade(msg):
    text = msg.text.strip()
    if '设置交易指令' not in text:
        return bot.reply_to(msg,
            "请按格式发送：\n"
            "设置交易指令\n"
            "设置汇率：0\n"
            "设置费率：0\n"
            "中介佣金：0.0"
        )
    try:
        rate = float(re.search(r'设置汇率[:：]\s*([\d.]+)', text).group(1))
        fee  = float(re.search(r'设置费率[:：]\s*([\d.]+)', text).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([\d.]+)', text).group(1))
    except:
        return bot.reply_to(msg, "❌ 格式错误，请严格按指示填写。")
    
    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        cursor.execute(
            """
            INSERT INTO settings(chat_id,user_id,rate,fee_rate,commission_rate)
            VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(chat_id,user_id) DO UPDATE
              SET rate=EXCLUDED.rate, fee_rate=EXCLUDED.fee_rate,
                  commission_rate=EXCLUDED.commission_rate
            """,
            (chat_id,user_id,rate,fee,comm)
        )
        conn.commit()
        bot.reply_to(msg, f"✅ 设置成功\n汇率：{rate}\n费率：{fee}%\n佣金率：{comm}%")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 入账（记账） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        # 1) 获取设置
        cursor.execute(
            "SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        s = cursor.fetchone()
        if not s:
            return bot.reply_to(msg, "❌ 请先设置交易参数。")

        # 2) 解析金额
        m = re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)
        if not m:
            return bot.reply_to(msg, "❌ 格式示例：+1000 或 入1000")
        amount = float(m[0])

        # 3) 计算基本数据
        currency, rate = s['currency'], s['rate']
        fee_rate, comm_rate = s['fee_rate'], s['commission_rate']
        after_fee = amount * (1 - fee_rate/100)
        usdt_val  = round(after_fee / rate, 2)
        comm_rmb  = round(amount * (comm_rate/100), 2)
        comm_usdt = round(comm_rmb / rate, 2)

        # 4) 时间和流水号
        tz = pytz.timezone('Asia/Kuala_Lumpur')
        now_local  = datetime.now(tz)
        t_str      = now_local.strftime('%H:%M:%S')
        today_date = now_local.date()

        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        cnt = cursor.fetchone()['cnt'] + 1
        tid = str(cnt).zfill(3)

        # 5) 插入交易记录
        cursor.execute("""
            INSERT INTO transactions
              (chat_id,user_id,amount,rate,fee_rate,commission_rate,
               currency,message_id,deducted_amount)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            chat_id, user_id, amount, rate, fee_rate, comm_rate,
            currency, msg.message_id, after_fee
        ))
        conn.commit()

        # 6) 获取所有记录
        cursor.execute("""
          SELECT id, date, amount, fee_rate, rate, name
          FROM transactions
          WHERE chat_id=%s AND user_id=%s
          ORDER BY date
        """, (chat_id, user_id))
        all_rows = cursor.fetchall()

        # 筛选“今日入笔”
        daily_lines = []
        for r in all_rows:
            rd = r['date']
            if rd is None:
                continue
            if rd.date() != today_date:
                continue
            ts = rd.strftime('%H:%M:%S')
            amt = r['amount']
            net = amt * (1 - r['fee_rate']/100)
            usd = round(net / r['rate'], 2)
            sign = '+' if amt > 0 else '-'
            daily_lines.append(
                f"{r['id']:03d}. {ts} {sign}{abs(amt)} * "
                f"{1 - r['fee_rate']/100} / {r['rate']} = {usd}  {r['name']}"
            )

        daily_cnt = len(daily_lines)
        total_amt = sum([r['amount'] for r in all_rows])
        total_pending = sum([r['deducted_amount'] for r in all_rows])

        # 7) 汇总并展示
        res = f"✅ 已入款 +{amount} ({currency})\n\n编号：{tid}\n\n"
        res += f"{tid}. {t_str} {amount} * {1-fee_rate/100} / {rate} = {usdt_val}  {msg.from_user.username}\n"
        if comm_rate > 0:
            res += f"{tid}. {t_str} {amount} * {comm_rate/100} = {comm_rmb} 【佣金】\n\n"

        res += f"今日入笔（{daily_cnt}笔）\n"
        if daily_cnt:
            res += "\n".join(daily_lines) + "\n"
        res += f"\n已入款（{len(all_rows)}笔）：{total_amt} (RMB)\n"
        res += f"汇率：{rate}\n费率：{fee_rate}%\n佣金：{comm_rmb} ({currency}) | {comm_usdt} USDT\n\n"
        res += f"应下发：{total_pending} ({currency}) | {round(total_pending / rate, 2)} (USDT)\n"

        bot.reply_to(msg, res)

    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 启动 —— #
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
