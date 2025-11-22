import os
import time
import jwt
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# Robust import of shared package (works in Docker and local)
try:
    from shared.db import get_conn, init_tables
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
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

def _current_user_row():
    auth = request.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '):
        return None, ('Missing or invalid Authorization header', 401)
    token = auth.split(' ', 1)[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except Exception:
        return None, ('Invalid or expired token', 401)
    user_id = decoded.get('sub')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        return None, ('User not found', 404)
    return row, None

@app.get('/users/me')
def get_me():
    row, err = _current_user_row()
    if err:
        return jsonify({'detail': err[0], 'code': 'UNAUTHORIZED' if err[1] == 401 else 'NOT_FOUND'}), err[1]
    return jsonify({
        'id': row[0],
        'username': row[1],
        'email': row[2],
        'full_name': row[3],
        'role': row[4],
        'created_at': row[5].isoformat() if row[5] else None
    })

@app.patch('/users/me')
def update_me():
    row, err = _current_user_row()
    if err:
        return jsonify({'detail': err[0], 'code': 'UNAUTHORIZED' if err[1] == 401 else 'NOT_FOUND'}), err[1]
    data = request.get_json() or {}
    fields = {}
    if 'email' in data and data['email'] and data['email'] != row[2]:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = %s AND id <> %s", (data['email'], row[0]))
        email_taken = cur.fetchone() is not None
        cur.close(); conn.close()
        if email_taken:
            return jsonify({'detail': 'email already in use', 'code': 'CONFLICT'}), 409
        fields['email'] = data['email']
    if 'full_name' in data and data['full_name']:
        fields['full_name'] = data['full_name']
    if 'password' in data and data['password']:
        fields['password_hash'] = generate_password_hash(data['password'])
    if not fields:
        return jsonify({'detail': 'No valid fields to update', 'code': 'BAD_REQUEST'}), 400
    set_parts = []
    values = []
    for k, v in fields.items():
        set_parts.append(f"{k} = %s")
        values.append(v)
    values.append(row[0])
    conn = get_conn(); cur = conn.cursor()
    cur.execute(f"UPDATE users SET {', '.join(set_parts)} WHERE id = %s RETURNING id, username, email, full_name, role, created_at", tuple(values))
    updated = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return jsonify({
        'id': updated[0],
        'username': updated[1],
        'email': updated[2],
        'full_name': updated[3],
        'role': updated[4],
        'created_at': updated[5].isoformat() if updated[5] else None
    })

@app.delete('/users/me')
def delete_me():
    row, err = _current_user_row()
    if err:
        return jsonify({'detail': err[0], 'code': 'UNAUTHORIZED' if err[1] == 401 else 'NOT_FOUND'}), err[1]
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (row[0],))
    conn.commit(); cur.close(); conn.close()
    return ('', 204)

@app.get('/users/<username>')
def get_user_by_username(username):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users WHERE username = %s", (username,))
    r = cur.fetchone(); cur.close(); conn.close()
    if not r:
        return jsonify({'detail': 'user not found', 'code': 'NOT_FOUND'}), 404
    return jsonify({
        'id': r[0],
        'username': r[1],
        'email': r[2],
        'full_name': r[3],
        'role': r[4],
        'created_at': r[5].isoformat() if r[5] else None
    })

@app.get('/users/<username>/bookings')
def user_booking_history(username):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_row = cur.fetchone()
    if not user_row:
        cur.close(); conn.close()
        return jsonify({'detail': 'user not found', 'code': 'NOT_FOUND'}), 404
    user_id = user_row[0]
    cur.execute("""
        SELECT b.id, r.name, b.start_time, b.end_time, b.status
        FROM bookings b
        JOIN rooms r ON b.room_id = r.id
        WHERE b.user_id = %s
        ORDER BY b.start_time DESC
    """, (user_id,))
    rows = cur.fetchall(); cur.close(); conn.close()
    bookings = []
    for b in rows:
        bookings.append({
            'id': b[0],
            'room_name': b[1],
            'start_time': b[2].isoformat() if b[2] else None,
            'end_time': b[3].isoformat() if b[3] else None,
            'status': b[4]
        })
    return jsonify(bookings)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8001))
    app.run(host='0.0.0.0', port=port)
