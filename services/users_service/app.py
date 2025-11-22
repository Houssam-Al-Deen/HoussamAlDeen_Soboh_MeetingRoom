import os
import time
import jwt
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from shared.db import get_conn, init_tables

DB_USER = os.getenv("POSTGRES_USER", "smr")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "smr_pass")
DB_NAME = os.getenv("POSTGRES_DB", "smart_meeting_room")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
JWT_EXP_SECONDS = 3600

app = Flask(__name__)
init_tables()

@app.post('/users/register')
def register_user():
    data = request.get_json() or {}
    required = ['username', 'email', 'password']
    if any(k not in data or not data[k] for k in required):
        return jsonify({'detail': 'username, email, password required', 'code': 'BAD_REQUEST'}), 400
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, email, full_name, role, password_hash) VALUES (%s, %s, %s, %s, %s) RETURNING id, username, email, full_name, role, created_at",
            (
                data['username'],
                data['email'],
                data.get('full_name'),
                data.get('role', 'user'),
                generate_password_hash(data['password'])
            )
        )
        row = cur.fetchone()
        conn.commit()
    except Exception as e:
        conn.rollback()
        cur.close(); conn.close()
        if 'unique' in str(e).lower():
            return jsonify({'detail': 'username or email already exists', 'code': 'CONFLICT'}), 409
        return jsonify({'detail': 'server error', 'code': 'SERVER_ERROR'}), 500
    cur.close(); conn.close()
    return jsonify({
        'id': row[0],
        'username': row[1],
        'email': row[2],
        'full_name': row[3],
        'role': row[4],
        'created_at': row[5].isoformat() if row[5] else None
    }), 201

@app.get('/users')
def list_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users ORDER BY id ASC")
    rows = cur.fetchall()
    cur.close(); conn.close()
    users = []
    for r in rows:
        users.append({
            'id': r[0],
            'username': r[1],
            'email': r[2],
            'full_name': r[3],
            'role': r[4],
            'created_at': r[5].isoformat() if r[5] else None
        })
    return jsonify(users)

@app.post('/auth/login')
def login():
    data = request.get_json() or {}
    if 'username' not in data or 'password' not in data:
        return jsonify({'detail': 'username and password required', 'code': 'BAD_REQUEST'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, role, password_hash FROM users WHERE username = %s", (data['username'],))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row or not check_password_hash(row[4], data['password']):
        return jsonify({'detail': 'Invalid credentials', 'code': 'UNAUTHORIZED'}), 401
    payload = {
        'sub': row[0],
        'username': row[1],
        'role': row[3],
        'exp': int(time.time()) + JWT_EXP_SECONDS
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
    return jsonify({'access_token': token, 'token_type': 'bearer'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8001))
    app.run(host='0.0.0.0', port=port)
