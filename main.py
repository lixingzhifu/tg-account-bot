# —— 1 导入与配置 —— #
import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
import pytz
from datetime import datetime

# —— 2 环境变量 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CUSTOMER_HELP_URL = "https://your.support.link"
CUSTOMER_CUSTOM_URL = "https://your.custom.link"

# —— 3 初始化 Bot 与数据库 —— #
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 4 建表：settings 和 transactions —— #
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    currency TEXT NOT NULL,
    rate DOUBLE PRECISION NOT NULL,
    fee_rate DOUBLE PRECISION NOT NULL,
    commission_rate DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(chat_id, user_id)
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('deposit','delete','issue','delete_issue')) DEFAULT 'deposit',
    amount DOUBLE PRECISION NOT NULL,
    after_fee DOUBLE PRECISION NOT NULL,
    commission_rmb DOUBLE PRECISION NOT NULL,
    commission_usdt DOUBLE PRECISION NOT NULL,
    deducted_amount DOUBLE PRECISION NOT NULL,
    rate DOUBLE PRECISION NOT NULL,
    fee_rate DOUBLE PRECISION NOT NULL,
    commission_rate DOUBLE PRECISION NOT NULL,
    currency TEXT NOT NULL,
    date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""")

# 确保 action 列存在
cursor.execute("""
ALTER TABLE transactions
ADD COLUMN IF NOT EXISTS action TEXT NOT NULL
CHECK (action IN ('deposit','delete','issue','delete_issue')) DEFAULT 'deposit';
""")
conn.commit()

# —— 辅助：回滚 —— #
def rollback():
    try:
        conn.rollback()
    except:
        pass

# —— 5 /start —— #
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add('/trade', '/commands')
    kb.add('/reset', '/show')
    kb.add('/help_customer', '/custom')
    bot.reply_to(msg,
        "欢迎使用 LX 记账机器人 ✅\n请选择菜单：",
        reply_markup=kb
    )

# —— 6 /commands —— #
@bot.message_handler(commands=['commands'])
def cmd_commands(msg):
    text = (
        "📖 指令大全：\n"
        "/start - 启动机器人\n"
        "/trade - 设置交易参数\n"
        "/reset - 清空所有记录\n"
        "/show - 显示今日账单\n"
        "+1000 或 入笔1000 - 记入款\n"
        "删除1000 或 撤销入款1000 - 删除入款\n"
        "下发1000 或 下发-1000 - 记录/撤销下发\n"
        "/help_customer - 客服帮助\n"
        "/custom - 定制机器人"
    )
    bot.reply_to(msg, text)

# —— 7 /trade 设置 —— #
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

@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def handle_trade_setup(msg):
    try:
        curr = re.search(r'设置货币[:：]\s*(\S+)', msg.text).group(1)
        rate = float(re.search(r'设置汇率[:：]\s*([0-9.]+)', msg.text).group(1))
        fee  = float(re.search(r'设置费率[:：]\s*([0-9.]+)', msg.text).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([0-9.]+)', msg.text).group(1))
    except:
        return bot.reply_to(msg, "❌ 格式错误，请严格按指示填写。")
    cid, uid = msg.chat.id, msg.from_user.id
    try:
        cursor.execute(
            "INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)"
            " VALUES(%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT(chat_id,user_id) DO UPDATE SET"
            " currency=EXCLUDED.currency, rate=EXCLUDED.rate,"
            " fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate",
            (cid, uid, curr, rate, fee, comm)
        )
        conn.commit()
        bot.reply_to(msg, f"✅ 设置成功\n货币：{curr}\n汇率：{rate}\n费率：{fee}%\n佣金率：{comm}%")
    except Exception as e:
        rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— 8 /reset —— #
@bot.message_handler(commands=['reset'])
def cmd_reset(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    try:
        cursor.execute(
            "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
            (cid, uid)
        )
        conn.commit()
        bot.reply_to(msg, "✅ 记录已清零！所有交易数据已删除。")
    except Exception as e:
        rollback()
        bot.reply_to(msg, f"❌ 重置失败：{e}")

# —— 9 /show —— #
@bot.message_handler(commands=['show'])
def cmd_show(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    today = datetime.now(tz).date()
    try:
        cursor.execute(
            "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date",
            (cid, uid)
        )
        rows = cursor.fetchall()
    except Exception as e:
        rollback()
        return bot.reply_to(msg, f"❌ 查询失败：{e}")
    dep_lines, iss_lines = [], []
    total_dep = total_pending = total_comm = total_iss = 0.0
    for r in rows:
        dt = r['date']
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=pytz.utc)
        local = dt.astimezone(tz)
        if r['action']=='deposit':
            total_dep += r['amount']
            total_pending += r['after_fee']
            total_comm += r['commission_rmb']
        elif r['action']=='delete':
            total_dep -= r['amount']
            total_pending -= r['after_fee']
            total_comm -= r['commission_rmb']
        elif r['action']=='issue':
            total_iss += r['amount']
        elif r['action']=='delete_issue':
            total_iss -= r['amount']
        if local.date()==today:
            ts = local.strftime('%H:%M:%S')
            if r['action'] in ('deposit','delete'):
                sign = '+' if r['action']=='deposit' else '-'
                usd_val = round(r['after_fee']/r['rate'],2)
                dep_lines.append(f"{r['id']:03d}. {ts} {sign}{abs(r['amount'])} * {1-r['fee_rate']/100} / {r['rate']} = {usd_val}  {r['name']}")
            else:
                sign = '+' if r['action']=='issue' else '-'
                usd_iss = round(r['amount']/r['rate'],2)
                iss_lines.append(f"{ts} {sign}{r['amount']} | {sign}{usd_iss} (USDT)  {r['name']}")
    pending = total_pending - total_iss
    text = []
    text.append(f"日入笔（{len(dep_lines)}笔）")
    text.extend(dep_lines or ["无"])  
    text.append(f"\n今日下发（{len(iss_lines)}笔）")
    text.extend(iss_lines or ["无"])  
    text.extend([
        "\n汇总：",
        f"已入款：{total_dep} (RMB)",
        f"应下发：{total_pending} (RMB)",
        f"已下发：{total_iss} (RMB)",
        f"未下发：{pending} (RMB)",
        f"累计佣金：{total_comm} (RMB)"
    ])
    bot.reply_to(msg, "\n".join(text))

# —— 10 客服 & 定制 —— #
@bot.message_handler(commands=['help_customer'])
def cmd_help(msg):
    bot.reply_to(msg, f"客服帮助：{CUSTOMER_HELP_URL}")
@bot.message_handler(commands=['custom'])
def cmd_custom(msg):
    bot.reply_to(msg, f"定制机器人：{CUSTOMER_CUSTOM_URL}")

# —— 11 统一操作入口 —— #
@bot.message_handler(func=lambda m: re.match(r'^(?:[\+入笔]?\d+(?:\.\d+)?|删除\d+(?:\.\d+)?|撤销入款\d+(?:\.\d+)?|下发-?\d+(?:\.\d+)?|删除下发\d+(?:\.\d+)?)$', m.text or ''))
def handle_action(msg):
    text = msg.text.strip()
    cid, uid = msg.chat.id, msg.from_user.id
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s", (cid, uid))
    s = cursor.fetchone()
    if not s:
        return bot.reply_to(msg, "❌ 请先 /trade 设置参数。")
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    now = datetime.now(tz)
    if re.match(r'^[\+入笔]?(\d+(?:\.\d+)?)$', text): action='deposit';     amt=float(re.sub(r'[\+入笔]','', text))
    elif re.match(r'^(?:删除|撤销入款)(\d+(?:\.\d+)?)$', text):   action='delete';      amt=float(re.sub(r'删除|撤销入款','', text))
    elif re.match(r'^下发(-?\d+(?:\.\d+)?)$', text):            action='issue';       amt=float(text.replace('下发',''))
    elif re.match(r'^删除下发(\d+(?:\.\d+)?)$', text):         action='delete_issue'; amt=float(text.replace('删除下发',''))
    else: return
    fee_rate, comm_rate, rate = s['fee_rate'], s['commission_rate'], s['rate']
    after_fee = amt * (1 - fee_rate/100)
    comm_rmb  = abs(amt) * (comm_rate/100)
    comm_usdt = round(comm_rmb / rate, 2)
    deducted_amount = amt if action in ('issue','delete_issue') else after_fee
    try:
        cursor.execute(
            "INSERT INTO transactions(chat_id,user_id,name,action,amount,after_fee,commission_rmb,commission_usdt,deducted_amount,rate,fee_rate,commission_rate,currency)"
            " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (cid, uid, msg.from_user.username, action, amt, after_fee, comm_rmb, comm_usdt, deducted_amount, rate, fee_rate, comm_rate, s['currency'])
        )
        conn.commit()
        bot.reply_to(msg, "✅ 操作成功！")
    except Exception as e:
        rollback()
        bot.reply_to(msg, f"❌ 失败：{e}")

# —— 12 启动 —— #
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
