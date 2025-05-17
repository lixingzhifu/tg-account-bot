import os
import re
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import TeleBot, types
import pytz
from datetime import datetime, timedelta

# —— 配置 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
CUSTOMER_HELP_URL = "https://your.support.link"
CUSTOMER_CUSTOM_URL = "https://your.custom.link"

# —— 初始化 —— #
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 建表 —— #
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
  action           TEXT    NOT NULL CHECK (action IN ('deposit','delete','issue','delete_issue')),
  amount           DOUBLE PRECISION NOT NULL,
  after_fee        DOUBLE PRECISION NOT NULL,
  commission_rmb   DOUBLE PRECISION NOT NULL,
  commission_usdt  DOUBLE PRECISION NOT NULL,
  deducted_amount  DOUBLE PRECISION NOT NULL,
  deducted_usdt    DOUBLE PRECISION NOT NULL,
  rate             DOUBLE PRECISION NOT NULL,
  fee_rate         DOUBLE PRECISION NOT NULL,
  commission_rate  DOUBLE PRECISION NOT NULL,
  currency         TEXT    NOT NULL,
  date             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

# —— /start —— #
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton('/trade'), types.KeyboardButton('/commands'))
    kb.add(types.KeyboardButton('/reset'), types.KeyboardButton('/show'))
    kb.add(types.KeyboardButton('/help_customer'), types.KeyboardButton('/custom'))
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择菜单：", reply_markup=kb)

# —— /commands —— #
@bot.message_handler(commands=['commands'])
def cmd_commands(msg):
    text = (
        "📖 指令大全：\n"
        "/start - 启动机器人\n"
        "/trade - 设置交易参数\n"
        "/reset - 清空所有记录\n"
        "/show - 显示今日账单\n"
        "+1000 / 入笔1000 - 记入款\n"
        "删除1000 / 撤销入款1000 - 删除一笔入款\n"
        "下发1000 / 下发-1000 - 记录/撤销下发\n"
        "/help_customer - 客服帮助\n"
        "/custom - 定制机器人\n"
    )
    bot.reply_to(msg, text)

# —— /trade —— #
@bot.message_handler(func=lambda m: re.match(r'^(/trade|设置交易)', m.text or ''))
def cmd_trade(msg):
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
        curr = re.search(r'设置货币[:：]\s*([^\s\n]+)', text).group(1)
        rate = float(re.search(r'设置汇率[:：]\s*([\d.]+)', text).group(1))
        fee  = float(re.search(r'设置费率[:：]\s*([\d.]+)', text).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([\d.]+)', text).group(1))
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
        conn.rollback()
        bot.reply_to(msg, f"❌ 存储失败：{e}")

# —— /reset —— #
@bot.message_handler(commands=['reset','calculate_reset'])
def cmd_reset(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    try:
        cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s", (cid, uid))
        conn.commit()
        bot.reply_to(msg, "✅ 记录已清零！")
    except Exception as e:
        conn.rollback()
        bot.reply_to(msg, f"❌ 重置失败：{e}")

# —— /show —— #
@bot.message_handler(commands=['show','显示账单'])
def cmd_show(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    today = datetime.now(tz).date()
    cursor.execute("SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date", (cid, uid))
    rows = cursor.fetchall()

    dep_lines = []
    iss_lines = []
    total_dep = total_pending = total_comm = total_iss = 0.0
    for r in rows:
        rd = r['date']
        if rd is None: continue
        if rd.tzinfo is None: rd = rd.replace(tzinfo=pytz.utc)
        local = rd.astimezone(tz)
        # 总计不分日期
        if r['action']=='deposit':
            total_dep += r['amount']
            total_pending += r['after_fee']
            total_comm += r['commission_rmb']
        elif r['action']=='delete':
            total_dep -= r['amount']
            total_pending -= r['after_fee']
            total_comm -= r['commission_rmb']
        elif r['action']=='issue':
            total_iss += r['deducted_amount']
        elif r['action']=='delete_issue':
            total_iss -= r['deducted_amount']
        # 今日明细
        if local.date()==today:
            ts = local.strftime('%H:%M:%S')
            if r['action'] in ('deposit','delete'):
                sign = '+' if r['action']=='deposit' else '-'
                usd = round(r['after_fee']/r['rate'],2)
                dep_lines.append(f"{r['id']:03d}. {ts} {sign}{abs(r['amount'])} * {1-r['fee_rate']/100} / {r['rate']} = {usd}  {r['name']}")
            else:
                sign = '+' if r['action']=='issue' else '-'
                ud = round(r['deducted_amount']/r['rate'],2)
                iss_lines.append(f"{ts} {sign}{r['deducted_amount']} | {sign}{ud} (USDT)  {r['name']}")

    res = [f"日入笔（{len(dep_lines)}笔）"] + (dep_lines or ["无"]) + ["\n今日下发（%d笔）"%len(iss_lines)] + (iss_lines or ["无"])
    tp = total_pending - total_iss
    res += ["\n汇总：", f"已入款：{total_dep}(RMB)", f"应下发：{total_pending}(RMB)",
            f"已下发：{total_iss}(RMB)", f"未下发：{tp}(RMB)", f"累计佣金：{total_comm}(RMB)"]
    bot.reply_to(msg, "\n".join(res))

# —— 客服 & 定制 —— #
@bot.message_handler(commands=['help_customer'])
def cmd_help(msg): bot.reply_to(msg, f"客服帮助：{CUSTOMER_HELP_URL}")

@bot.message_handler(commands=['custom'])
def cmd_custom(msg): bot.reply_to(msg, f"定制机器人：{CUSTOMER_CUSTOM_URL}")

# —— 入款/删除入款 —— #
@bot.message_handler(func=lambda m: re.match(r'^(?:[\+入笔]?\d+(?:\.\d+)?|删除\d+(?:\.\d+)?|撤销入款\d+(?:\.\d+)?|入款-\d+(?:\.\d+)?)$', m.text or ''))
def handle_deposit(msg):
    text=msg.text.strip()
    m1=re.match(r'^(?:[\+入笔]?)(\d+(?:\.\d+)?)$',text)
    m2=re.match(r'^(?:删除|撤销入款|入款-)(\d+(?:\.\d+)?)$',text)
    if m1: amt=float(m1.group(1)); act='deposit'
    else: amt=float(m2.group(1)); act='delete'
    # 插入逻辑
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",(msg.chat.id,msg.from_user.id))
    s=cursor.fetchone()
    if not s: return bot.reply_to(msg,"❌ 请先 /trade 设置参数。")
    # 计算
    after=amt*(1-s['fee_rate']/100)
    cr=s['commission_rate']/100*amt
    cu=round(cr/s['rate'],2)
    da=abs(after) if act=='issue' else after
    du=abs(da)/s['rate']
    #插入
    cursor.execute("""
INSERT INTO transactions(chat_id,user_id,name,action,amount,after_fee,commission_rmb,commission_usdt,deducted_amount,deducted_usdt,rate,fee_rate,commission_rate,currency)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
        (msg.chat.id,msg.from_user.id,msg.from_user.username,act,amt,after,cr,cu,0,0,s['rate'],s['fee_rate'],s['commission_rate'],s['currency'])
    )
    conn.commit()
    bot.reply_to(msg,f"✅ 已{'删除' if act=='delete' else '入'}款 {amt} ({s['currency']})")

# —— 下发/删除下发 —— #
@bot.message_handler(func=lambda m: re.match(r'^(?:下发-?\d+(?:\.\d+)?|删除下发\d+(?:\.\d+)?)$',m.text or ''))
def handle_issue(msg):
    text=msg.text.strip()
    m1=re.match(r'^下发(-?\d+(?:\.\d+)?)$',text)
    m2=re.match(r'^删除下发(\d+(?:\.\d+)?)$',text)
    if m1: val=float(m1.group(1)); act='issue'
    else: val=float(m2.group(1)); act='delete_issue'
    # 设置
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",(msg.chat.id,msg.from_user.id))
    s=cursor.fetchone()
    if not s: return bot.reply_to(msg,"❌ 请先 /trade 设置参数。")
    da=val
    du=round(val/s['rate'],2)
    #插入
    cursor.execute("""
INSERT INTO transactions(chat_id,user_id,name,action,amount,after_fee,commission_rmb,commission_usdt,deducted_amount,deducted_usdt,rate,fee_rate,commission_rate,currency)
VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""",
        (msg.chat.id,msg.from_user.id,msg.from_user.username,act,0,0,0,0,da,du,s['rate'],s['fee_rate'],s['commission_rate'],s['currency'])
    )
    conn.commit()
    bot.reply_to(msg,f"✅ 已{'删除下发' if act=='delete_issue' else '下发'} {val} ({s['currency']})")

# —— 启动 —— #
if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
