import os
import psycopg2

DB_USER = os.getenv("POSTGRES_USER", "smr")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "smr_pass")
DB_NAME = os.getenv("POSTGRES_DB", "smart_meeting_room")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")

def get_conn():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )

_initialized = False

TABLE_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username VARCHAR(50) UNIQUE NOT NULL,
        email VARCHAR(120) UNIQUE NOT NULL,
        full_name VARCHAR(120),
        role VARCHAR(20) DEFAULT 'user',
        password_hash VARCHAR(255) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rooms (
        id SERIAL PRIMARY KEY,
        name VARCHAR(80) UNIQUE NOT NULL,
        capacity INTEGER NOT NULL,
        equipment TEXT,
        location VARCHAR(120),
        is_active BOOLEAN DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bookings (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
        start_time TIMESTAMPTZ NOT NULL,
        end_time TIMESTAMPTZ NOT NULL,
        status VARCHAR(20) DEFAULT 'active',
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reviews (
        id SERIAL PRIMARY KEY,
        room_id INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        comment TEXT,
        is_flagged BOOLEAN DEFAULT FALSE,
        flag_reason TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
]

def init_tables():
    global _initialized
    if _initialized:
        return
    conn = get_conn(); conn.autocommit = True
    cur = conn.cursor()
    for stmt in TABLE_STATEMENTS:
        cur.execute(stmt)
    cur.close(); conn.close()
    _initialized = True
