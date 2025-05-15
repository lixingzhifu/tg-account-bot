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
  deducted_amount  DOUBLE PRECISION DEFAULT 0.0
);
""")
conn.commit()

# —— /start —— #
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('/trade', '/reset')
    bot.reply_to(msg,
        "欢迎使用 LX 记账机器人 ✅\n"
        "请选择：\n"
        "/trade — 设置交易参数\n"
        "+数字 — 入账记录\n"
        "/reset — 清零所有记录",
        reply_markup=kb
    )

# —— /trade —— #
@bot.message_handler(commands=['trade'])
def cmd_trade(msg):
    bot.reply_to(msg,
        "请按格式发送：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0.0"
    )

# —— 设置交易参数 —— #
@bot.message_handler(func=lambda m: m.text and m.text.startswith("设置交易指令"))
def handle_trade(msg):
    text = msg.text.replace('：',':')
    try:
        currency = re.search(r'设置货币:([^\s]+)', text).group(1)
        rate     = float(re.search(r'设置汇率:([\d.]+)', text).group(1))
        fee      = float(re.search(r'设置费率:([\d.]+)', text).group(1))
        comm     = float(re.search(r'中介佣金:([\d.]+)', text).group(1))
    except:
        return bot.reply_to(msg, "❌ 格式错误，示例：\n设置交易指令\n设置货币：RMB\n设置汇率：9\n设置费率：2\n中介佣金：0.5")

    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        cursor.execute("""
          INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
          VALUES(%s,%s,%s,%s,%s,%s)
          ON CONFLICT(chat_id,user_id) DO UPDATE
            SET currency=EXCLUDED.currency, rate=EXCLUDED.rate,
                fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate
        """, (chat_id, user_id, currency, rate, fee, comm))
        conn.commit()
        bot.reply_to(msg, f"✅ 设置成功\n货币：{currency}\n汇率：{rate}\n费率：{fee}%\n佣金率：{comm}%")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 入账（记录交易并显示今日摘要） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]+\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id

    try:
        # 1) 读取设置
        cursor.execute(
            "SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        s = cursor.fetchone()
        if not s:
            return bot.reply_to(msg, "❌ 请先 /trade 设置交易参数。")

        # —— 确保下面能用到的变量都已定义 —— #
        currency   = s['currency']
        rate       = s['rate']
        fee_rate   = s['fee_rate']
        comm_rate  = s['commission_rate']

        # 2) 解析入账金额
        amt = float(re.findall(r'[\+入笔]+([\d.]+)', msg.text)[0])
        after_fee = round(amt * (1 - fee_rate/100), 2)

        # 3) 写入交易
        cursor.execute("""
          INSERT INTO transactions
            (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,
             currency,message_id,deducted_amount)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            chat_id, user_id, msg.from_user.username,
            amt, rate, fee_rate, comm_rate,
            currency, msg.message_id, after_fee
        ))
        conn.commit()

        # 4) 汇总全量：总笔数 & 总入款 & 总应下发
        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM transactions WHERE chat_id=%s AND user_id=%s",
            (chat_id, user_id)
        )
        cnt = cursor.fetchone()['cnt']
        cursor.execute("""
          SELECT SUM(amount) AS sum_amt, SUM(deducted_amount) AS sum_pending
          FROM transactions WHERE chat_id=%s AND user_id=%s
        """, (chat_id, user_id))
        agg = cursor.fetchone()
        total_amt     = float(agg['sum_amt']     or 0)
        total_pending = float(agg['sum_pending'] or 0)
        total_issued  = 0.0
        total_unissued= total_pending
        tp_usdt = round(total_pending  / rate, 2)
        ti_usdt = round(total_issued   / rate, 2)
        tu_usdt = round(total_unissued / rate, 2)

        # 5) 取“今日”UTC 时间范围
        import pytz
        from datetime import datetime, timedelta
        tz = pytz.timezone('Asia/Kuala_Lumpur')
        now_loc    = datetime.now(tz)
        start_loc  = now_loc.replace(hour=0, minute=0, second=0, microsecond=0)
        end_loc    = start_loc + timedelta(days=1)
        start_utc  = start_loc.astimezone(pytz.utc)
        end_utc    = end_loc.astimezone(pytz.utc)

        # 拉取今日所有入笔/删单 且累加佣金
        cursor.execute("""
          SELECT id, date, amount, fee_rate, rate, name, commission_rate
          FROM transactions
          WHERE chat_id=%s
            AND user_id=%s
            AND date >= %s AND date < %s
          ORDER BY date
        """, (chat_id, user_id, start_utc, end_utc))
        rows = cursor.fetchall()

        lines = []
        positive_count = 0
        total_comm_rmb = 0.0
        for r in rows:
            a = r['amount']
            sign   = '+' if a>0 else '-'
            abs_a  = abs(a)
            after2 = abs_a * (1 - r['fee_rate']/100)
            u2     = round(after2 / r['rate'], 2)
            # 将 UTC 存储的 timestamp 转本地显示
            dt_utc = r['date'].replace(tzinfo=pytz.utc)
            ts     = dt_utc.astimezone(tz).strftime('%H:%M:%S')

            lines.append(
                f"{r['id']:03d}. {ts}  {sign}{abs_a} * {1 - r['fee_rate']/100} / {r['rate']} = {u2}  {r['name']}"
            )
            if a>0: positive_count += 1
            total_comm_rmb += abs_a * (r['commission_rate']/100)

        # 6) 构造并发送结果
        text  = f"今日入笔（{positive_count}笔）\n"
        text += ("\n".join(lines)+"\n\n") if lines else "\n\n"
        text += "今日下发（0笔）\n\n"
        text += (
            f"已入款（{cnt}笔）：{total_amt} ({currency})\n\n"
            f"应下发：{total_pending} ({currency}) | {tp_usdt} (USDT)\n"
            f"已下发：{total_issued} ({currency}) | {ti_usdt} (USDT)\n"
            f"未下发：{total_unissued} ({currency}) | {tu_usdt} (USDT)\n\n"
            f"佣金应下发：{round(total_comm_rmb,2)} ({currency}) | {round(total_comm_rmb/rate,2)} (USDT)\n"
            f"佣金已下发：0.0 ({currency}) | 0.00 (USDT)\n"
            f"佣金未下发：{round(total_comm_rmb,2)} ({currency}) | {round(total_comm_rmb/rate,2)} (USDT)\n"
        )
        bot.reply_to(msg, text)
        return

    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 重置所有记录 —— #
@bot.message_handler(commands=['reset', 'calculate_reset'])
def cmd_reset(msg):
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
                   (msg.chat.id, msg.from_user.id))
    conn.commit()
    bot.reply_to(msg, "✅ 记录已清零！")

# —— 启动轮询 —— #
if __name__ == '__main__':
    bot.delete_webhook()
    bot.remove_webhook()
    bot.infinity_polling()
