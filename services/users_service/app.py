"""Users service
-----------------

Authentication, registration, self-service profile updates, and admin-only
user management.
"""

import os
import time
import jwt
import functools
from flask import Flask, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash

# Robust import of shared package (works in Docker and local)
try:
    from shared.db import get_conn, init_tables
    from shared.errors import install_error_handlers, APIError
    from shared.rate_limit import rate_limit
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from shared.db import get_conn, init_tables
    from shared.errors import install_error_handlers, APIError
    from shared.rate_limit import rate_limit

DB_USER = os.getenv("POSTGRES_USER", "smr")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "smr_pass")
DB_NAME = os.getenv("POSTGRES_DB", "smart_meeting_room")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
JWT_EXP_SECONDS = 3600

app = Flask(__name__)
# API version prefix variable (e.g. /api/v1). Default includes '/api/'.
_raw_ver = os.getenv('API_VERSION', 'v1').strip('/')
API_PREFIX = f"/api/{_raw_ver}" if not _raw_ver.startswith('api/') else f"/{_raw_ver}"
# Avoid DB side effects during Sphinx autodoc
if os.getenv('DOCS_BUILD') != '1':
    init_tables()
install_error_handlers(app)

ALLOWED_ROLES = {"admin", "user", "moderator"}

# ---------------- Helper utilities ----------------

def user_row_to_json(row):
    """Convert a user table row tuple to a JSON-serializable dict.

    :param row: Database row tuple ``(id, username, email, full_name, role, created_at)``.
    :returns: User dictionary without password information.
    """
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
    """Create a signed JWT for a given user row.

    The payload includes ``sub`` (user id), ``username``, ``role``, and ``exp``.

    :param row: Database user row.
    :returns: JWT string (HS256).
    :rtype: str
    """
    return jwt.encode({
        'sub': row[0],
        'username': row[1],
        'role': row[4],
        'exp': int(time.time()) + JWT_EXP_SECONDS
    }, JWT_SECRET, algorithm='HS256')

def _decode_token():
    """Decode and validate the JWT from the ``Authorization`` header.

    Raises APIError on failure; returns decoded payload on success.

    :returns: ``dict`` payload
    """
    auth = request.headers.get('Authorization')
    if not auth or not auth.startswith('Bearer '):
        raise APIError('auth required', status=401, code='auth_required')
    token = auth.split(' ', 1)[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except Exception:
        raise APIError('invalid token', status=401, code='invalid_token')
    return decoded

def require_auth(fn):
    """Decorator enforcing JWT authentication.

    On success, stores the decoded payload in ``request._auth``.

    :param fn: The route function to wrap.
    :returns: Wrapped function.
    """
    @functools.wraps(fn)
    def inner(*a, **kw):
        info = _decode_token()
        request._auth = info
        return fn(*a, **kw)
    return inner

def require_roles(*roles):
    """Decorator enforcing that caller's role is among ``roles``.

    Also validates the JWT and sets ``request._auth`` on success.

    :param roles: Allowed role names.
    :returns: Wrapped function.
    """
    def deco(fn):
        @functools.wraps(fn)
        def inner(*a, **kw):
            info = _decode_token()
            if info.get('role') not in roles:
                raise APIError('forbidden', status=403, code='forbidden')
            request._auth = info
            return fn(*a, **kw)
        return inner
    return deco

@app.post(f"{API_PREFIX}/users/register")
@rate_limit(5, 60, key='ip')
def register_user():
    """Register a new user.

    Admin/mode roles can be created only by an admin token (except first admin).

    :request body: JSON with ``username``, ``email``, ``password``; optional ``full_name``, ``role``.
    :returns: Created user JSON (no password).
    :raises 400: Missing fields or invalid role name.
    :raises 403: Admin token required for admin/moderator signup after bootstrap.
    :raises 409: Username or email already exists.
    :raises 500: Unexpected database error.
    """
    data = request.get_json() or {}
    required = ['username', 'email', 'password']
    if any(k not in data or not data[k] for k in required):
        raise APIError('username, email, password required', status=400, code='validation_error')
    role = data.get('role', 'user')
    if role not in ALLOWED_ROLES:
        raise APIError('invalid role', status=400, code='validation_error')
    conn = get_conn(); cur = conn.cursor()
    # Restrict privileged role self-signup: only allow if requester is admin.
    # Bootstrap rule: if no admin exists yet, first admin may be created without token.
    if role in ('admin', 'moderator'):
        cur.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1")
        has_admin = cur.fetchone() is not None
        if has_admin or role == 'moderator':
            auth_hdr = request.headers.get('Authorization')
            if not auth_hdr or not auth_hdr.startswith('Bearer '):
                cur.close(); conn.close(); raise APIError('admin token required', status=403, code='forbidden')
            info = _decode_token()
            if info.get('role') != 'admin':
                cur.close(); conn.close(); raise APIError('admin token required', status=403, code='forbidden')
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
            raise APIError('username or email exists', status=409, code='conflict')
        raise APIError('server error', status=500, code='server_error')
    cur.close(); conn.close()
    return jsonify(user_row_to_json(row)), 201

@app.get(f"{API_PREFIX}/users")
@require_roles('admin')
def list_users():
    """List all users (admin only).

    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is not an admin.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users ORDER BY id ASC")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([user_row_to_json(r) for r in rows])

@app.get(f"{API_PREFIX}/users/id/<int:user_id>/status")
def user_status(user_id: int):
    """Lightweight existence/status endpoint for inter-service validation.

    Returns 200 with minimal payload if the user exists, otherwise 404.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        raise APIError('user not found', status=404, code='not_found')
    return jsonify({'id': row[0], 'username': row[1], 'status': 'ok'})

@app.post(f"{API_PREFIX}/auth/login")
@rate_limit(10, 60, key='ip')
def login():
    """Issue a JWT for valid credentials.

    :request body: JSON with ``username`` and ``password``.
    :returns: JSON with ``access_token`` and ``token_type``.

    :raises 400: Missing credentials.
    :raises 401: Invalid credentials.
    """
    data = request.get_json() or {}
    if 'username' not in data or 'password' not in data:
        raise APIError('username and password required', status=400, code='validation_error')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, password_hash, created_at FROM users WHERE username = %s", (data['username'],))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row or not check_password_hash(row[5], data['password']):
        raise APIError('invalid credentials', status=401, code='invalid_credentials')
    token = _make_token(row)
    return jsonify({'access_token': token, 'token_type': 'bearer'})

def _current_user_row():
    """Fetch the current authenticated user's row or raise 404.

    Uses the JWT subject id from ``request._auth`` and raises APIError(404)
    if the user no longer exists.

    :returns: ``tuple`` user row
    """
    info = _decode_token()
    user_id = info.get('sub')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone(); cur.close(); conn.close()
    if not row:
        raise APIError('user not found', status=404, code='not_found')
    return row

@app.get(f"{API_PREFIX}/users/me")
@require_auth
def get_me():
    """Return the authenticated user's profile.

    :raises 401: Missing/invalid token.
    :raises 404: Authenticated user no longer exists.
    """
    row = _current_user_row()
    return jsonify(user_row_to_json(row))

@app.patch(f"{API_PREFIX}/users/me")
@require_auth
def update_me():
    """Update your own profile.

    Accepts ``email``, ``full_name``, and ``password``.

    :raises 400: No valid fields provided or invalid email/password format.
    :raises 401: Missing/invalid token.
    :raises 404: Authenticated user no longer exists.
    :raises 409: Email already in use by another user.
    """
    row = _current_user_row()
    data = request.get_json() or {}
    fields = {}
    if 'email' in data and data['email'] and data['email'] != row[2]:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE email = %s AND id <> %s", (data['email'], row[0]))
        if cur.fetchone():
            cur.close(); conn.close();
            raise APIError('email already in use', status=409, code='conflict')
        cur.close(); conn.close();
        fields['email'] = data['email']
    if 'full_name' in data and data['full_name']:
        fields['full_name'] = data['full_name']
    if 'password' in data and data['password']:
        fields['password_hash'] = generate_password_hash(data['password'])
    if not fields:
        raise APIError('no valid fields', status=400, code='validation_error')
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

@app.delete(f"{API_PREFIX}/users/me")
@require_auth
def delete_me():
    """Delete your own account.

    :raises 401: Missing/invalid token.
    :raises 404: Authenticated user not found.
    """
    row = _current_user_row()
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE id = %s", (row[0],))
    conn.commit(); cur.close(); conn.close()
    return ('', 204)

@app.patch(f"{API_PREFIX}/users/<username>")
@require_roles('admin')
def admin_update_user(username):
    """Admin updates another user's fields.

    Accepts ``email``, ``full_name``, ``password``, and ``role``.

    :raises 400: Invalid role or no fields provided.
    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is not an admin.
    :raises 404: Target user not found.
    :raises 409: Email already in use.
    :raises 500: Unexpected database error.
    """
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
            raise APIError('invalid role', status=400, code='validation_error')
        fields['role'] = data['role']
    if not fields:
        raise APIError('no fields provided', status=400, code='validation_error')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_row = cur.fetchone()
    if not user_row:
        cur.close(); conn.close(); raise APIError('user not found', status=404, code='not_found')
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
            raise APIError('email already in use', status=409, code='conflict')
        raise APIError('server error', status=500, code='server_error')
    cur.close(); conn.close()
    return jsonify(user_row_to_json(updated))

@app.delete(f"{API_PREFIX}/users/<username>")
@require_roles('admin')
def admin_delete_user(username):
    """Admin deletes a user by username.

    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is not an admin.
    :raises 404: User not found.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM users WHERE username = %s RETURNING id", (username,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); raise APIError('user not found', status=404, code='not_found')
    conn.commit(); cur.close(); conn.close()
    return jsonify({'detail': 'deleted', 'id': row[0]})

@app.get(f"{API_PREFIX}/users/<username>")
@require_auth
def get_user_by_username(username):
    """Get a user by username (self or admin).

    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is neither the target user nor admin.
    :raises 404: User not found.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, username, email, full_name, role, created_at FROM users WHERE username = %s", (username,))
    r = cur.fetchone(); cur.close(); conn.close()
    if not r:
        raise APIError('user not found', status=404, code='not_found')
    # Allow if self or admin
    if request._auth.get('username') != username and request._auth.get('role') != 'admin':
        raise APIError('forbidden', status=403, code='forbidden')
    return jsonify(user_row_to_json(r))

@app.get(f"{API_PREFIX}/users/<username>/bookings")
@require_auth
def user_booking_history(username):
    """Return a user's booking history (self or admin).

    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is neither the target user nor admin.
    :raises 404: User not found.
    """
    if request._auth.get('username') != username and request._auth.get('role') != 'admin':
        raise APIError('forbidden', status=403, code='forbidden')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username = %s", (username,))
    user_row = cur.fetchone()
    if not user_row:
        cur.close(); conn.close(); raise APIError('user not found', status=404, code='not_found')
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
