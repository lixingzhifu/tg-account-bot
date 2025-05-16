import os, re, pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telebot import TeleBot, types

# —— 配置 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 建表 —— #
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id BIGINT, user_id BIGINT,
  rate DOUBLE PRECISION, fee_rate DOUBLE PRECISION, commission_rate DOUBLE PRECISION,
  PRIMARY KEY(chat_id,user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
  id SERIAL PRIMARY KEY, chat_id BIGINT, user_id BIGINT,
  amount DOUBLE PRECISION, rate DOUBLE PRECISION, fee_rate DOUBLE PRECISION, commission_rate DOUBLE PRECISION,
  date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, message_id BIGINT,
  status TEXT DEFAULT 'pending', deducted_amount DOUBLE PRECISION DEFAULT 0.0
);
""")
conn.commit()

# —— 工具函数 —— #
def fetch_settings(chat_id,user_id):
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",
                   (chat_id,user_id))
    return cursor.fetchone()

def fetch_all(chat_id,user_id):
    cursor.execute("""
      SELECT * FROM transactions
      WHERE chat_id=%s AND user_id=%s ORDER BY date
    """,(chat_id,user_id))
    return cursor.fetchall()

def format_summary(chat_id,user_id):
    # 获取所有交易
    rows = fetch_all(chat_id,user_id)
    # 今日日期
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    today = datetime.now(tz).date()

    # 分组
    pending = []
    deleted = []
    for r in rows:
        if r['status']=='pending' and r['date'].date()==today:
            amt = r['amount']
            ts = r['date'].astimezone(tz).strftime('%H:%M:%S')
            usd = round((amt*(1-r['fee_rate']/100))/r['rate'],2)
            pending.append(f"{r['id']:03d}. {ts} +{amt} * {1-r['fee_rate']/100} / {r['rate']} = {usd}  {r['name']}")
        if r['status']=='deleted' and r['date'].date()==today:
            amt = r['amount']
            ts = r['date'].astimezone(tz).strftime('%H:%M:%S')
            usd = round((amt*(1-r['fee_rate']/100))/r['rate'],2)
            deleted.append(f"{r['id']:03d}. {ts} -{amt} * {1-r['fee_rate']/100} / {r['rate']} = {usd}  {r['name']}")

    # 汇总计算
    total_amt      = sum(r['amount'] for r in rows if r['status']=='pending')
    total_comm_rmb = sum(r['amount']*r['commission_rate']/100 for r in rows if r['status']=='pending')
    total_pending  = sum(r['amount']*(1-r['fee_rate']/100) for r in rows if r['status']=='pending')
    total_issued   = 0.0
    total_unissued = total_pending - total_issued
    rate = rows[0]['rate'] if rows else fetch_settings(chat_id,user_id)['rate']
    fee_rate = fetch_settings(chat_id,user_id)['fee_rate']
    comm_rate= fetch_settings(chat_id,user_id)['commission_rate']

    lines  = []
    lines.append(f"今日入笔（{len(pending)}笔）")
    lines += pending
    if deleted:
        lines += deleted
    lines.append("")
    lines.append(f"已入款（{len(pending)}笔）：{total_amt} (RMB)")
    lines.append(f"汇率：{rate}")
    lines.append(f"费率：{fee_rate}%")
    lines.append(f"佣金：{round(total_comm_rmb,2)} | {round(total_comm_rmb/rate,2)} USDT")
    lines.append("")
    lines.append(f"应下发：{round(total_pending,2)} | {round(total_pending/rate,2)} (USDT)")
    lines.append(f"已下发：{round(total_issued,2)} | {round(total_issued/rate,2)} (USDT)")
    lines.append(f"未下发：{round(total_unissued,2)} | {round(total_unissued/rate,2)} (USDT)")
    lines.append("")
    lines.append(f"佣金应下发：{round(total_comm_rmb,2)} | {round(total_comm_rmb/rate,2)} (USDT)")
    lines.append(f"佣金已下发：0.0 | 0.00 (USDT)")
    lines.append(f"佣金未下发：{round(total_comm_rmb,2)} | {round(total_comm_rmb/rate,2)} (USDT)")
    return "\n".join(lines)

# —— 命令：指令大全 —— #
@bot.message_handler(commands=['指令大全'])
def cmd_help(msg):
    help_text = (
        "📜 指令大全：\n"
        "/start  启动机器人\n"
        "/trade  设置交易指令\n"
        "+1000 / 入1000 / 入笔1000  入账\n"
        "删除1000 / 撤销入款  删除最近一笔\n"
        "删除编号001  删除指定编号（仅当天）\n"
        "/reset / /calculate_reset  清空今日记录\n"
        "/显示账单  查看今日汇总\n"
        "/客服帮助  获取客服链接\n"
        "/定制机器人  获取定制链接"
    )
    bot.reply_to(msg, help_text)

# —— 命令：客服帮助 & 定制 —— #
@bot.message_handler(commands=['客服帮助'])
def cmd_cs(msg):
    bot.reply_to(msg, "请联系客服：<客服链接>")

@bot.message_handler(commands=['定制机器人'])
def cmd_custom(msg):
    bot.reply_to(msg, "定制机器人请访问：<定制链接>")

# —— 命令：重置 —— #
@bot.message_handler(commands=['reset','calculate_reset'])
def cmd_reset(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",(cid,uid))
    conn.commit()
    bot.reply_to(msg, "✅ 今日记录已重置")

# —— 命令：显示账单 —— #
@bot.message_handler(commands=['显示账单'])
def cmd_summary(msg):
    bot.reply_to(msg, format_summary(msg.chat.id,msg.from_user.id))

# —— 入账 —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    s = fetch_settings(cid,uid)
    if not s:
        return bot.reply_to(msg, "❌ 请先 /trade 设置交易参数")
    m = re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)[0]
    amount = float(m)
    # 计算
    after_fee = amount*(1-s['fee_rate']/100)
    # 插入
    cursor.execute("""
      INSERT INTO transactions
        (chat_id,user_id,amount,rate,fee_rate,commission_rate,
         date,message_id,deducted_amount)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (cid,uid,amount,s['rate'],s['fee_rate'],s['commission_rate'],
          datetime.now(),msg.message_id,after_fee))
    conn.commit()
    # 反馈
    bot.reply_to(msg,
        f"✅ 已入款 +{amount}.0 (RMB)\n\n"
        + format_summary(cid,uid)
    )

# —— 删除最近一笔 —— #
@bot.message_handler(func=lambda m: re.match(r'^(删除|撤销入款)\d+(\.\d+)?$', m.text or ''))
def handle_delete(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    m = re.findall(r'(\d+(\.\d+)?)', msg.text)[0][0]
    amount = float(m)
    # 找最近一笔 pending
    cursor.execute("""
      SELECT id,amount,date,name,rate,fee_rate FROM transactions
      WHERE chat_id=%s AND user_id=%s AND status='pending'
      ORDER BY date DESC LIMIT 1
    """,(cid,uid))
    row = cursor.fetchone()
    if not row:
        return bot.reply_to(msg,"❌ 无可删除的入账记录")
    # 标记删除
    cursor.execute("""
      UPDATE transactions SET status='deleted'
      WHERE id=%s
    """,(row['id'],))
    conn.commit()
    # 反馈
    summary = format_summary(cid,uid)
    bot.reply_to(msg,
        f"✅ 订单{row['id']:03d']} 已删除 -{row['amount']}.0 (RMB)\n\n"
        + summary
    )

if __name__=='__main__':
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
