import os
from flask import Flask, request, jsonify
import psycopg2
from werkzeug.security import generate_password_hash

DB_USER = os.getenv("POSTGRES_USER", "smr")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "smr_pass")
DB_NAME = os.getenv("POSTGRES_DB", "smart_meeting_room")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")

app = Flask(__name__)

CREATE_USERS_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    full_name VARCHAR(120),
    role VARCHAR(20) DEFAULT 'user',
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
)
"""

def get_conn():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )

_tables_init = False

def init_tables():
    global _tables_init
    if _tables_init:
        return
    conn = get_conn(); conn.autocommit = True
    cur = conn.cursor()
    cur.execute(CREATE_USERS_SQL)
    cur.close(); conn.close()
    _tables_init = True

@app.route('/users/register', methods=['POST'])
def register_user():
    data = request.get_json() or {}
    for field in ['username','email','password']:
        if field not in data:
            return jsonify({'detail':'Missing required fields','code':'BAD_REQUEST'}), 400
    init_tables()
    password_hash = generate_password_hash(data['password'])
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username,email,full_name,role,password_hash) VALUES (%s,%s,%s,%s,%s) RETURNING id, username, email, role",(
            data['username'], data['email'], data.get('full_name'), data.get('role','user'), password_hash
        ))
        row = cur.fetchone(); conn.commit()
        return jsonify({'id':row[0],'username':row[1],'email':row[2],'role':row[3]}), 201
    except psycopg2.errors.UniqueViolation:
        conn.rollback(); return jsonify({'detail':'Username or email exists','code':'CONFLICT'}), 409
    except Exception:
        conn.rollback(); return jsonify({'detail':'Server error','code':'SERVER_ERROR'}), 500
    finally:
        cur.close(); conn.close()

@app.route('/users', methods=['GET'])
def list_users():
    init_tables()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, role FROM users ORDER BY id")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify([
        {'id': r[0], 'username': r[1], 'email': r[2], 'role': r[3]} for r in rows
    ])

if __name__ == '__main__':
    port = int(os.getenv('PORT',8001))
    app.run(host='0.0.0.0', port=port)
