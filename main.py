import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
import pytz
from datetime import datetime

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
        cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE
          SET currency=EXCLUDED.currency, rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate
        """, (chat_id,user_id,currency,rate,fee,comm))
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

from datetime import datetime, timedelta
import pytz
import re

# —— 入账（记录交易） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    chat_id, user_id = msg.chat.id, msg.from_user.id
    try:
        # 读取设置
        cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",
                       (chat_id, user_id))
        s = cursor.fetchone()
        if not s:
            return bot.reply_to(msg, "❌ 请先 /trade 设置参数。")

        # 解析金额
        arr = re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)
        if not arr:
            return bot.reply_to(msg, "❌ 格式示例：+1000 或 入1000")
        amount = float(arr[0])

        # 计算基本数据
        rate, fee_rate, comm_rate = s['rate'], s['fee_rate'], s['commission_rate']
        currency = s['currency']
        after_fee = amount * (1 - fee_rate/100)

        # 插入记录
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

        # ----- 下面开始“今日入笔”筛选 -----
        # 马来西亚时区“今日”起止（UTC）
        malaysia = pytz.timezone('Asia/Kuala_Lumpur')
        now_local = datetime.now(malaysia)
        today = now_local.date()
        start_local = malaysia.localize(datetime.combine(today, datetime.min.time()))
        end_local = start_local + timedelta(days=1)
        start_utc = start_local.astimezone(pytz.utc)
        end_utc   = end_local.astimezone(pytz.utc)

                # ----- 下面开始“今日入笔”和“佣金累积”筛选 -----
        # 取出该用户所有交易
        cursor.execute("""
            SELECT id, date, amount, fee_rate, rate, name, commission_rate
            FROM transactions
            WHERE chat_id = %s AND user_id = %s
            ORDER BY date
        """, (chat_id, user_id))
        all_rows = cursor.fetchall()

        malaysia = pytz.timezone('Asia/Kuala_Lumpur')
        today_local = datetime.now(malaysia).date()

        lines = []
        positive_count = 0
        total_comm_rmb = 0.0

        for r in all_rows:
            # 转成本地时间再判定是否“今日”
            local_dt = r['date'].astimezone(malaysia)
            if local_dt.date() != today_local:
                continue

            amt = r['amount']
            sign = '+' if amt > 0 else '-'
            abs_amt = abs(amt)
            after_fee = abs_amt * (1 - r['fee_rate']/100)
            usdt = round(after_fee / r['rate'], 2)
            ts = local_dt.strftime('%H:%M:%S')

            # 加入列表
            lines.append(
                f"{r['id']:03d}. {ts}  {sign}{abs_amt} * {1 - r['fee_rate']/100} / {r['rate']} = {usdt}  {r['name']}"
            )
            if amt > 0:
                positive_count += 1

            # 累积佣金，本地币
            total_comm_rmb += abs_amt * (r['commission_rate']/100)

        # 构造“今日入笔”与“今日下发”部分
        res  = f"今日入笔（{positive_count}笔）\n"
        if lines:
            res += "\n".join(lines) + "\n\n"
        else:
            res += "\n"
        res += "今日下发（0笔）\n\n"

        # —— 底部原统计保持不变 —— #
        res += (
            f"已入款（{cnt}笔）：{total_amt} ({currency})\n\n"
            f"应下发：{total_pending} ({currency}) | {tp_usdt} (USDT)\n"
            f"已下发：{total_issued} ({currency}) | {ti_usdt} (USDT)\n"
            f"未下发：{total_unissued} ({currency}) | {tu_usdt} (USDT)\n\n"
            f"佣金应下发：{round(total_comm_rmb,2)} ({currency}) | "
            f"{round(total_comm_rmb/rate,2)} (USDT)\n"
            f"佣金已下发：0.0 ({currency}) | 0.00 (USDT)\n"
            f"佣金未下发：{round(total_comm_rmb,2)} ({currency}) | "
            f"{round(total_comm_rmb/rate,2)} (USDT)\n"
        )

        bot.reply_to(msg, res)
        return

    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 启动 —— #
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling()
