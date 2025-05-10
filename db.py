import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# --- 建表 ---
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id         BIGINT,
    user_id         BIGINT,
    currency        TEXT    DEFAULT 'RMB',
    rate            DOUBLE PRECISION DEFAULT 0,
    fee_rate        DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id               SERIAL PRIMARY KEY,
    chat_id          BIGINT,
    user_id          BIGINT,
    name             TEXT,
    amount           DOUBLE PRECISION,
    rate             DOUBLE PRECISION,
    fee_rate         DOUBLE PRECISION,
    commission_rate  DOUBLE PRECISION,
    currency         TEXT,
    created_at       TIMESTAMP,
    message_id       BIGINT
);
""")
conn.commit()

# --- Settings 操作 ---
def get_settings(chat_id, user_id):
    cursor.execute("""
      SELECT currency, rate, fee_rate, commission_rate
        FROM settings
       WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    return cursor.fetchone()

def upsert_settings(chat_id, user_id, currency, rate, fee, commission):
    cursor.execute("""
      INSERT INTO settings
        (chat_id,user_id,currency,rate,fee_rate,commission_rate)
      VALUES (%s,%s,%s,%s,%s,%s)
      ON CONFLICT (chat_id,user_id) DO UPDATE SET
        currency = EXCLUDED.currency,
        rate     = EXCLUDED.rate,
        fee_rate = EXCLUDED.fee_rate,
        commission_rate = EXCLUDED.commission_rate
    """, (chat_id, user_id, currency, rate, fee, commission))
    conn.commit()

# --- Transactions 操作 ---
def add_transaction(chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, created_at, message_id):
    cursor.execute("""
      INSERT INTO transactions
        (chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,created_at,message_id)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      RETURNING id
    """, (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, created_at, message_id))
    new_id = cursor.fetchone()["id"]
    conn.commit()
    return new_id

def delete_latest(chat_id, user_id):
    cursor.execute("""
      SELECT id FROM transactions
       WHERE chat_id=%s AND user_id=%s
       ORDER BY created_at DESC
       LIMIT 1
    """, (chat_id, user_id))
    row = cursor.fetchone()
    if not row:
        return None
    cursor.execute("DELETE FROM transactions WHERE id=%s", (row["id"],))
    conn.commit()
    return row["id"]

def delete_by_id(chat_id, user_id, tid):
    cursor.execute("""
      DELETE FROM transactions
       WHERE chat_id=%s AND user_id=%s AND id=%s
      RETURNING id
    """, (chat_id, user_id, tid))
    row = cursor.fetchone()
    conn.commit()
    return row and row["id"]

def fetch_all(chat_id, user_id):
    cursor.execute("""
      SELECT * FROM transactions
       WHERE chat_id=%s AND user_id=%s
       ORDER BY created_at
    """, (chat_id, user_id))
    return cursor.fetchall()
