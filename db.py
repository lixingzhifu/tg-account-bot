# db.py

import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 初始化表（无害，可多次运行）
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT     DEFAULT 'RMB',
    rate     DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    user_id BIGINT,
    name    TEXT,
    amount  DOUBLE PRECISION,
    rate    DOUBLE PRECISION,
    fee_rate       DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date     TIMESTAMP DEFAULT NOW()
);
""")
conn.commit()
