import os
import time
import jwt
import functools
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

ALLOWED_ROLES = {"admin", "user", "moderator"}

# ---------------- Helper utilities ----------------

def user_row_to_json(row):
    return {
        'id': row[0],
        'username': row[1],
        'email': row[2],
        'full_name': row[3],
        'role': row[4],
        'created_at': row[5].isoformat() if row[5] else None
    }
# (Removed generic update helper to keep code explicit for students)

def _make_token(row):
    return jwt.encode({
        'sub': row[0],
        'username': row[1],
        'role': row[4],
        'exp': int(time.time()) + JWT_EXP_SECONDS
    }, JWT_SECRET, algorithm='HS256')

def _decode_token():
    auth = request.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '):
        return None, ('auth required', 401)
    token = auth.split(' ', 1)[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except Exception:
        return None, ('invalid token', 401)
    return decoded, None

def require_auth(fn):
    @functools.wraps(fn)
    def inner(*a, **kw):
        info, err = _decode_token()
        if err:
            return jsonify({'detail': err[0]}), err[1]
        request._auth = info
        return fn(*a, **kw)
    return inner

def require_roles(*roles):
    def deco(fn):
        @functools.wraps(fn)
        def inner(*a, **kw):
            info, err = _decode_token()
            if err:
                return jsonify({'detail': err[0]}), err[1]
            if info.get('role') not in roles:
                return jsonify({'detail': 'forbidden'}), 403
            request._auth = info
            return fn(*a, **kw)
        return inner
    return deco

@app.post('/users/register')
def register_user():
    data = request.get_json() or {}
    required = ['username', 'email', 'password']
    if any(k not in data or not data[k] for k in required):
        return jsonify({'detail': 'username, email, password required'}), 400
    role = data.get('role', 'user')
    if role not in ALLOWED_ROLES:
        return jsonify({'detail': 'invalid role'}), 400
    conn = get_conn(); cur = conn.cursor()
    # Restrict privileged role self-signup: only allow if requester is admin.
    # Bootstrap rule: if no admin exists yet, first admin may be created without token.
    if role in ('admin', 'moderator'):
        cur.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")
        has_admin = cur.fetchone() is not None
        if has_admin or role == 'moderator':
            info, err = _decode_token()
            if err or info.get('role') != 'admin':
                cur.close(); conn.close(); return jsonify({'detail': 'admin token required'}), 403
    try:
        cur.execute(
            "INSERT INTO users (username, email, full_name, role, password_hash) VALUES (%s, %s, %s, %s, %s) RETURNING id, username, email, full_name, role, created_at",
            (
                data['username'],
                data['email'],
                data.get('full_name'),
                role,
                generate_password_hash(data['password'])
            )
        )
        row = cur.fetchone(); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        if 'unique' in str(e).lower():
            return jsonify({'detail': 'username or email exists'}), 409
        return jsonify({'detail': 'server error'}), 500
    cur.close(); conn.close()
    return jsonify(user_row_to_json(row)), 201

@app.get('/users')
@require_roles('admin')
def list_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users ORDER BY id ASC")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([user_row_to_json(r) for r in rows])

@app.post('/auth/login')
def login():
    data = request.get_json() or {}
    if 'username' not in data or 'password' not in data:
        return jsonify({'detail': 'username and password required'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, password_hash, created_at FROM users WHERE username = %s", (data['username'],))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row or not check_password_hash(row[5], data['password']):
        return jsonify({'detail': 'invalid credentials'}), 401
    token = _make_token(row)
    return jsonify({'access_token': token, 'token_type': 'bearer'})

def _current_user_row():
    info, err = _decode_token()
    if err:
        return None, err
    user_id = info.get('sub')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        return None, ('user not found', 404)
    return row, None

@app.get('/users/me')
@require_auth
def get_me():
    row, err = _current_user_row()
    if err:
        return jsonify({'detail': err[0]}), err[1]
    return jsonify(user_row_to_json(row))

@app.patch('/users/me')
@require_auth
def update_me():
    row, err = _current_user_row()
    if err:
        return jsonify({'detail': err[0]}), err[1]
    data = request.get_json() or {}
    fields = {}
    if 'email' in data and data['email'] and data['email'] != row[2]:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = %s AND id <> %s", (data['email'], row[0]))
        if cur.fetchone():
            cur.close(); conn.close()
            return jsonify({'detail': 'email already in use'}), 409
        cur.close(); conn.close()
        fields['email'] = data['email']
    if 'full_name' in data and data['full_name']:
        fields['full_name'] = data['full_name']
    if 'password' in data and data['password']:
        fields['password_hash'] = generate_password_hash(data['password'])
    if not fields:
        return jsonify({'detail': 'no valid fields'}), 400
    set_parts = []
    params = []
    for col, val in fields.items():
        set_parts.append(f"{col} = %s")
        params.append(val)
    params.append(row[0])  # id for WHERE
    sql = f"UPDATE users SET {', '.join(set_parts)} WHERE id = %s RETURNING id, username, email, full_name, role, created_at"
    conn = get_conn(); cur = conn.cursor()
    cur.execute(sql, tuple(params))
    updated = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(user_row_to_json(updated))

@app.delete('/users/me')
@require_auth
def delete_me():
    row, err = _current_user_row()
    if err:
        return jsonify({'detail': err[0]}), err[1]
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (row[0],))
    conn.commit(); cur.close(); conn.close()
    return ('', 204)

@app.patch('/users/<username>')
@require_roles('admin')
def admin_update_user(username):
    data = request.get_json() or {}
    fields = {}
    if 'email' in data and data['email']:
        fields['email'] = data['email']
    if 'full_name' in data and data['full_name']:
        fields['full_name'] = data['full_name']
    if 'password' in data and data['password']:
        fields['password_hash'] = generate_password_hash(data['password'])
    if 'role' in data and data['role']:
        if data['role'] not in ALLOWED_ROLES:
            return jsonify({'detail': 'invalid role'}), 400
        fields['role'] = data['role']
    if not fields:
        return jsonify({'detail': 'no fields provided'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_row = cur.fetchone()
    if not user_row:
        cur.close(); conn.close(); return jsonify({'detail': 'user not found'}), 404
    set_parts = []
    params = []
    for col, val in fields.items():
        set_parts.append(f"{col} = %s")
        params.append(val)
    params.append(username)
    sql = f"UPDATE users SET {', '.join(set_parts)} WHERE username = %s RETURNING id, username, email, full_name, role, created_at"
    try:
        cur.execute(sql, tuple(params))
        updated = cur.fetchone(); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        if 'unique' in str(e).lower():
            return jsonify({'detail': 'email already in use'}), 409
        return jsonify({'detail': 'server error'}), 500
    cur.close(); conn.close()
    return jsonify(user_row_to_json(updated))

@app.delete('/users/<username>')
@require_roles('admin')
def admin_delete_user(username):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username = %s RETURNING id", (username,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'detail': 'user not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify({'detail': 'deleted', 'id': row[0]})

@app.get('/users/<username>')
@require_auth
def get_user_by_username(username):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users WHERE username = %s", (username,))
    r = cur.fetchone(); cur.close(); conn.close()
    if not r:
        return jsonify({'detail': 'user not found'}), 404
    # Allow if self or admin
    if request._auth.get('username') != username and request._auth.get('role') != 'admin':
        return jsonify({'detail': 'forbidden'}), 403
    return jsonify(user_row_to_json(r))

@app.get('/users/<username>/bookings')
@require_auth
def user_booking_history(username):
    if request._auth.get('username') != username and request._auth.get('role') != 'admin':
        return jsonify({'detail': 'forbidden'}), 403
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_row = cur.fetchone()
    if not user_row:
        cur.close(); conn.close(); return jsonify({'detail': 'user not found'}), 404
    user_id = user_row[0]
    cur.execute("""
        SELECT b.id, r.name, b.start_time, b.end_time, b.status
        FROM bookings b
        JOIN rooms r ON b.room_id = r.id
        WHERE b.user_id = %s
        ORDER BY b.start_time DESC
    """, (user_id,))
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([
        {
            'id': b[0], 'room_name': b[1], 'start_time': b[2].isoformat() if b[2] else None,
            'end_time': b[3].isoformat() if b[3] else None, 'status': b[4]
        } for b in rows
    ])

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8001))
    app.run(host='0.0.0.0', port=port)
