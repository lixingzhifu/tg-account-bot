import os, re, math
from datetime import datetime
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN        = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
bot = telebot.TeleBot(TOKEN)

# 连接数据库
conn   = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 强制删除旧表，重建新表 —— 
cursor.execute("DROP TABLE IF EXISTS transactions")
cursor.execute("DROP TABLE IF EXISTS settings")
cursor.execute("""
CREATE TABLE settings (
  chat_id         BIGINT,
  user_id         BIGINT,
  currency        TEXT    DEFAULT 'RMB',
  rate            DOUBLE PRECISION DEFAULT 0,
  fee_rate        DOUBLE PRECISION DEFAULT 0,
  commission_rate DOUBLE PRECISION DEFAULT 0,
  PRIMARY KEY(chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE transactions (
  id               SERIAL      PRIMARY KEY,
  chat_id          BIGINT,
  user_id          BIGINT,
  name             TEXT,
  amount           DOUBLE PRECISION,
  rate             DOUBLE PRECISION,
  fee_rate         DOUBLE PRECISION,
  commission_rate  DOUBLE PRECISION,
  currency         TEXT,
  date             TIMESTAMP,
  message_id       BIGINT
);
""")
conn.commit()

def ceil2(x):
    return math.ceil(x * 100) / 100.0

# 读取配置
def get_settings(cid, uid):
    cursor.execute(
      "SELECT currency, rate, fee_rate, commission_rate "
      "FROM settings WHERE chat_id=%s AND user_id=%s",
      (cid, uid)
    )
    r = cursor.fetchone()
    if not r:
        return ('RMB', 0, 0, 0)
    return (r['currency'], r['rate'], r['fee_rate'], r['commission_rate'])

# 生成汇总
def show_summary(cid, uid):
    cursor.execute(
      "SELECT * FROM transactions "
      "WHERE chat_id=%s AND user_id=%s ORDER BY id",
      (cid, uid)
    )
    recs = cursor.fetchall()
    total = sum(r['amount'] for r in recs)
    cur, rate, fee, comm = get_settings(cid, uid)
    converted = ceil2(total*(1-fee/100)/rate) if rate else 0
    comm_rmb  = ceil2(total*comm/100)
    comm_usdt = ceil2(comm_rmb/rate) if rate else 0

    lines = []
    for idx, r in enumerate(recs, 1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount']*(1-r['fee_rate']/100)
        usdt = ceil2(after_fee/r['rate']) if r['rate'] else 0
        lines.append(f"{idx}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r['commission_rate']>0:
            c_amt = ceil2(r['amount']*r['commission_rate']/100)
            lines.append(f"{idx}. {t} {r['amount']}*{r['commission_rate']/100:.2f} = {c_amt} 【佣金】")

    body = "\n".join(lines)
    footer = (
        f"\n已入款（{len(recs)}笔）：{total} ({cur})\n"
        f"已下发（0笔）：0 (USDT)\n\n"
        f"总入款金额：{total} ({cur})\n"
        f"汇率：{rate}\n费率：{fee:.1f}%\n佣金：{comm:.1f}%\n\n"
        f"应下发：{ceil2(total*(1-fee/100))}({cur}) | {converted}(USDT)\n"
        f"已下发：0.0({cur}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({cur}) | {converted}(USDT)\n"
    )
    if comm>0:
        footer += f"\n中介佣金应下发：{comm_rmb}({cur}) | {comm_usdt}(USDT)"
    return body + footer

# ——————————————————
# /start
@bot.message_handler(commands=['start'])
def cmd_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易','📘 指令大全')
    kb.row('🔁 重启计算','📊 汇总')
    kb.row('❓ 帮助','🛠️ 定制')
    bot.send_message(m.chat.id, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

# /id
@bot.message_handler(commands=['id'])
def cmd_id(m):
    bot.reply_to(m, f"chat_id={m.chat.id}\nuser_id={m.from_user.id}")

# 显示模板
@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def cmd_show(m):
    tpl = (
      "设置交易指令\n"
      "设置货币：RMB\n"
      "设置汇率：0\n"
      "设置费率：0\n"
      "中介佣金：0"
    )
    bot.reply_to(m, tpl)

# 保存配置
@bot.message_handler(func=lambda m: '设置交易指令' in m.text)
def set_trade(m):
    t = m.text.replace('：',':')
    cur=rate=fee=comm=None; errs=[]
    for L in t.splitlines():
        L2=L.strip().replace(' ','')
        if L2.startswith('设置货币'):
            cur=re.sub(r'[^A-Za-z]','',L2.split(':',1)[1]).upper()
        if L2.startswith('设置汇率'):
            try: rate=float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append('汇率格式错误')
        if L2.startswith('设置费率'):
            try: fee=float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append('费率格式错误')
        if L2.startswith('中介佣金'):
            try: comm=float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append('中介佣金格式错误')
    if errs or rate is None:
        bot.reply_to(m, "设置错误\n" + "\n".join(errs or ['缺少汇率']))
        return

    cid,uid=m.chat.id,m.from_user.id
    cursor.execute("""
      INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
      VALUES(%s,%s,%s,%s,%s,%s)
      ON CONFLICT(chat_id,user_id) DO UPDATE
        SET currency=EXCLUDED.currency,
            rate=EXCLUDED.rate,
            fee_rate=EXCLUDED.fee_rate,
            commission_rate=EXCLUDED.commission_rate
    """, (cid,uid,cur,rate,fee,comm))
    conn.commit()
    bot.reply_to(m, (
      "✅ 设置成功\n"
      f"设置货币：{cur}\n"
      f"设置汇率：{rate:.1f}\n"
      f"设置费率：{fee:.1f}%\n"
      f"中介佣金：{comm:.1f}%"
    ))

# 入笔
@bot.message_handler(func=lambda m: re.match(r'^[+\-加]\s*\d',m.text) 
                            or re.search(r'\D+[+\-加]\s*\d',m.text))
def handle_amount(m):
    cid, uid = m.chat.id, m.from_user.id
    txt = m.text.strip()
    # 名称+数量 或 +数量
    m1 = re.match(r'^[+\-加]\s*(\d+(\.\d*)?)$', txt)
    if m1:
        amt = float(m1.group(1))
        name= m.from_user.username or m.from_user.first_name or '匿名'
    else:
        nm, num = re.split(r'[+\-加]', txt, 1)
        name = nm.strip() or (m.from_user.username or '匿名')
        amt  = float(re.findall(r'\d+(\.\d*)?',num)[0])

    cur, rate, fee, comm = get_settings(cid, uid)
    now = datetime.now()
    cursor.execute("""
      INSERT INTO transactions(
        chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id
      ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (cid,uid,name,amt,rate,fee,comm,cur,now,m.message_id))
    conn.commit()

    # 取当前笔数，做编号
    cursor.execute(
      "SELECT COUNT(*) AS cnt FROM transactions WHERE chat_id=%s AND user_id=%s",
      (cid,uid)
    )
    cnt = cursor.fetchone()['cnt']
    no  = f"{cnt:03d}"

    summary = show_summary(cid, uid)
    reply = (
      f"✅ 已入款 {amt:.1f} ({cur})\n"
      f"编号：{no}\n\n"
      + summary
    )
    bot.reply_to(m, reply)

# 启动轮询
bot.remove_webhook()
bot.infinity_polling()
