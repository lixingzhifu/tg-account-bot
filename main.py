import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
import pytz
from datetime import datetime, timedelta

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
  status           TEXT DEFAULT 'pending',
  deducted_amount  DOUBLE PRECISION DEFAULT 0.0
);
"""
)
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
            "设置货币：RMB\n"
            "设置汇率：0\n"
            "设置费率：0\n"
            "中介佣金：0.0"
        )
    try:
        currency = re.search(r'设置货币[:：]\s*([^\s\n]+)', text).group(1)
        rate = float(re.search(r'设置汇率[:：]\s*([\d.]+)', text).group(1))
        fee  = float(re.search(r'设置费率[:：]\s*([\d.]+)', text).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([\d.]+)', text).group(1))
    except:
        return bot.reply_to(msg, "❌ 格式错误，请严格按指示填写。")
    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        cursor.execute(
            """
            INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
            VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(chat_id,user_id) DO UPDATE
              SET currency=EXCLUDED.currency, rate=EXCLUDED.rate,
                  fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate
            """,
            (chat_id,user_id,currency,rate,fee,comm)
        )
        conn.commit()
        bot.reply_to(msg, f"✅ 设置成功\n货币：{currency}\n汇率：{rate}\n费率：{fee}%\n佣金率：{comm}%")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 计算重置 —— #
@bot.message_handler(commands=['calculate_reset', 'reset'])
def cmd_reset(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        cursor.execute(
            "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        conn.commit()
        bot.reply_to(msg, "✅ 记录已清零！所有交易数据已删除，从头开始计算。")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 重置失败：{e}")

# —— 入账（记录交易） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        # 取设置
        cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",
                       (chat_id,user_id))
        s = cursor.fetchone()
        if not s:
            return bot.reply_to(msg, "❌ 请先 /trade 设置交易参数。")

        # 解析金额
        m = re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)
        if not m:
            return bot.reply_to(msg, "❌ 格式示例：+1000 或 入1000")
        amount = float(m[0])

        # 参数
        currency, rate = s['currency'], s['rate']
        fee_rate, comm_rate = s['fee_rate'], s['commission_rate']
        after_fee = amount * (1 - fee_rate/100)
        usdt_val = round(after_fee/rate,2)
        comm_rmb = round(amount*(comm_rate/100),2)
        comm_usdt= round(comm_rmb/rate,2)

        # 时间与编号
        tz = pytz.timezone('Asia/Kuala_Lumpur')
        now_local = datetime.now(tz)
        t = now_local.strftime('%H:%M:%S')
        cursor.execute("SELECT COUNT(*) AS cnt FROM transactions WHERE chat_id=%s AND user_id=%s",
                       (chat_id,user_id))
        cnt = cursor.fetchone()['cnt'] + 1
        tid = str(cnt).zfill(3)

        # 插入
        cursor.execute("""
        INSERT INTO transactions
          (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,
           currency,message_id,deducted_amount)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            chat_id, user_id, msg.from_user.username,
            amount, rate, fee_rate, comm_rate,
            currency, msg.message_id, after_fee
        ))
        conn.commit()

        # 汇总 总入款 & 应下发
        cursor.execute("""
        SELECT SUM(amount) AS sa, SUM(deducted_amount) AS sp
        FROM transactions WHERE chat_id=%s AND user_id=%s
        """, (chat_id,user_id))
        row = cursor.fetchone()
        total_amt      = float(row['sa'] or 0)
        total_pending  = float(row['sp'] or 0)
        total_issued   = 0.0
        total_unissued = total_pending
        tp_usdt = round(total_pending/rate,2)
        ti_usdt = round(total_issued/rate,2)
        tu_usdt = round(total_unissued/rate,2)

           # —— “今日入笔” & “今日下发” —— #
    # 1) 先算当地“今天”在 UTC 的范围
    malaysia = pytz.timezone('Asia/Kuala_Lumpur')
    now_local = datetime.now(malaysia)
    today      = now_local.date()
    start_local = malaysia.localize(datetime.combine(today, datetime.min.time()))
    end_local   = start_local + timedelta(days=1)
    start_utc = start_local.astimezone(pytz.utc)
    end_utc   = end_local.astimezone(pytz.utc)

    # 2) 拉取今天的所有入笔记录（含正负）、并统计笔数
    cursor.execute("""
      SELECT id, date, amount, fee_rate, rate, name
      FROM transactions
      WHERE chat_id=%s AND user_id=%s
        AND date >= %s AND date < %s
      ORDER BY date
    """, (chat_id, user_id, start_utc, end_utc))
    today_rows = cursor.fetchall()

    daily_lines = []
    for r in today_rows:
        amt = r['amount']
        after = amt * (1 - r['fee_rate']/100)
        usdt  = round(after / r['rate'], 2)
        # 把数据库里的 UTC 时间转回本地时区再格式化
        ts = r['date'].replace(tzinfo=pytz.utc).astimezone(malaysia).strftime('%H:%M:%S')
        sign = '+' if amt>0 else ''
        daily_lines.append(f"{r['id']:03d}. {ts} {sign}{amt} * {1 - r['fee_rate']/100} / {r['rate']} = {usdt}  {r['name']}")
    daily_cnt = len(daily_lines)

    # 3) （今天暂不支持“已下发”明细，先留空）
    issued_cnt = 0

        # —— 构造回复 —— #
res  = f"✅ 已入款 +{amount} ({currency})\n\n编号：{tid}\n\n"
res += f"{tid}. {t} {amount} * {1-fee_rate/100} / {rate} = {usdt_val}  {msg.from_user.username}\n"
if comm_rate>0:
    res += f"{tid}. {t} {amount} * {comm_rate/100} = {comm_rmb} 【佣金】\n\n"

# —— 加上「今日入笔」与「今日下发」块 —— #
res += f"今日入笔（{daily_cnt}笔）\n"
if daily_lines:
    res += "\n" + "\n".join(daily_lines) + "\n\n"
res += f"今日下发（{issued_cnt}笔）\n\n"
        
        # 汇总数据
        res += (
            f"\n已入款（{cnt}笔）：{total_amt} ({currency})\n"
            f"\n应下发：{total_pending} ({currency}) | {tp_usdt} (USDT)\n"
            f"已下发：{total_issued} ({currency}) | {ti_usdt} (USDT)\n"
            f"未下发：{total_unissued} ({currency}) | {tu_usdt} (USDT)\n"
            f"\n中介佣金应下发：{comm_rmb} ({currency}) | {comm_usdt} (USDT)\n"
        )
        bot.reply_to(msg, res)

    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 启动 —— #
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
