import os
import time
import jwt
import functools
from flask import Flask, request, jsonify
from datetime import datetime, timezone

try:
    from shared.db import get_conn, init_tables
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from shared.db import get_conn, init_tables

app = Flask(__name__)
init_tables()  # bookings table already created in shared schema

JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
JWT_EXP_SECONDS = 3600



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

def _booking_row_to_dict(r):
    def _norm(dt):
        if dt is None:
            return None
        # Always return naive ISO string (no timezone suffix) for simplicity in tests
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.isoformat()
    return {
        'id': r[0],
        'user_id': r[1],
        'room_id': r[2],
        'start_time': _norm(r[3]),
        'end_time': _norm(r[4]),
        'status': r[5]
    }
#added these cuz of timezone issues
def _parse_iso(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None

def _to_naive(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        # Convert any aware datetime to UTC then drop tzinfo for simple comparisons
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

@app.post('/bookings')
@require_auth
def create_booking():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    room_id = data.get('room_id')
    start = data.get('start_time')
    end = data.get('end_time')


    if not (user_id and room_id and start and end):
        return jsonify({'detail': 'user_id, room_id, start_time, end_time required'}), 400

    # Non-admin users can only create bookings for themselves
    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role != 'admin' and user_id != auth_user_id:
        return jsonify({'detail': 'forbidden'}), 403
    
    start_dt = _to_naive(_parse_iso(start)); end_dt = _to_naive(_parse_iso(end))
    if not start_dt or not end_dt or end_dt <= start_dt:
        return jsonify({'detail': 'invalid times (ISO) or end <= start'}), 400
    conn = get_conn(); cur = conn.cursor()
    # Verify user and room exist 
    
    cur.execute('SELECT id FROM users WHERE id = %s', (user_id,))
    if not cur.fetchone():
        cur.close(); conn.close(); return jsonify({'detail': 'user not found'}), 404
    cur.execute('SELECT id FROM rooms WHERE id = %s', (room_id,))
    if not cur.fetchone():
        cur.close(); conn.close(); return jsonify({'detail': 'room not found'}), 404
    # Conflict detection
    cur.execute('''SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active'
                   AND start_time < %s AND end_time > %s LIMIT 1''', (room_id, end_dt, start_dt))
    if cur.fetchone():
        cur.close(); conn.close(); return jsonify({'detail': 'time slot conflict'}), 409
    cur.execute('''INSERT INTO bookings (user_id, room_id, start_time, end_time)
                   VALUES (%s, %s, %s, %s) RETURNING id, user_id, room_id, start_time, end_time, status''',
                (user_id, room_id, start_dt, end_dt))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row)), 201

@app.get('/bookings')
@require_auth
def list_bookings():
    conn = get_conn(); cur = conn.cursor()
    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role == 'admin':
        cur.execute('''SELECT b.id, b.user_id, b.room_id, b.start_time, b.end_time, b.status,
                       u.username, r.name
                       FROM bookings b
                       JOIN users u ON b.user_id = u.id
                       JOIN rooms r ON b.room_id = r.id
                       ORDER BY b.start_time ASC''')
    else:
        cur.execute('''SELECT b.id, b.user_id, b.room_id, b.start_time, b.end_time, b.status,
                       u.username, r.name
                       FROM bookings b
                       JOIN users u ON b.user_id = u.id
                       JOIN rooms r ON b.room_id = r.id
                       WHERE b.user_id = %s
                       ORDER BY b.start_time ASC''', (auth_user_id,))
    rows = cur.fetchall(); cur.close(); conn.close()
    out = []
    for r in rows:
        out.append({
            'id': r[0],
            'user_id': r[1],
            'room_id': r[2],
            'start_time': r[3].isoformat(),
            'end_time': r[4].isoformat(),
            'status': r[5],
            'username': r[6],
            'room_name': r[7]
        })
    return jsonify(out)

@app.patch('/bookings/<int:booking_id>')
@require_auth
def update_booking(booking_id):
    data = request.get_json() or {}
    new_room_id = data.get('room_id')
    start = data.get('start_time')
    end = data.get('end_time')
    force = data.get('force')  # admin override flag


    if not (new_room_id or start or end):
        return jsonify({'detail': 'no fields to update'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, user_id, room_id, start_time, end_time, status FROM bookings WHERE id = %s', (booking_id,))
    existing = cur.fetchone()


    if not existing:
        cur.close(); conn.close(); return jsonify({'detail': 'booking not found'}), 404

    owner_id = existing[1]
    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role != 'admin' and owner_id != auth_user_id:
        cur.close(); conn.close(); return jsonify({'detail': 'forbidden'}), 403

    if existing[5] != 'active' and not (role == 'admin' and force):
        cur.close(); conn.close(); return jsonify({'detail': 'cannot modify non-active booking'}), 400
    

    room_id = existing[2]
    start_dt = _to_naive(existing[3])
    end_dt = _to_naive(existing[4])

    if new_room_id:
        room_id = new_room_id
        cur.execute('SELECT id FROM rooms WHERE id = %s', (room_id,))
        if not cur.fetchone():
            cur.close(); conn.close(); return jsonify({'detail': 'room not found'}), 404
        
    if start:
        st = _parse_iso(start)
        if not st:
            cur.close(); conn.close(); return jsonify({'detail': 'invalid start_time'}), 400
        start_dt = _to_naive(st)

    if end:
        et = _parse_iso(end)
        if not et:
            cur.close(); conn.close(); return jsonify({'detail': 'invalid end_time'}), 400
        end_dt = _to_naive(et)
    if end_dt <= start_dt:
        cur.close(); conn.close(); return jsonify({'detail': 'end_time must be after start_time'}), 400
    
    # Conflict check excluding this booking (skip if admin force)
    if not (role == 'admin' and force):
        cur.execute('''SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active' AND id <> %s
                       AND start_time < %s AND end_time > %s LIMIT 1''', (room_id, booking_id, end_dt, start_dt))
        if cur.fetchone():
            cur.close(); conn.close(); return jsonify({'detail': 'time slot conflict'}), 409
    cur.execute('''UPDATE bookings SET room_id = %s, start_time = %s, end_time = %s, updated_at = NOW()
                   WHERE id = %s RETURNING id, user_id, room_id, start_time, end_time, status''',
                (room_id, start_dt, end_dt, booking_id))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row))



@app.delete('/bookings/<int:booking_id>')
@require_auth
def cancel_booking(booking_id):
    # Soft cancel: set status = 'canceled'
    conn = get_conn(); cur = conn.cursor()
    # Ownership/admin check
    cur.execute('SELECT user_id, status FROM bookings WHERE id = %s', (booking_id,))
    owner_row = cur.fetchone()
    if not owner_row:
        cur.close(); conn.close(); return jsonify({'detail': 'booking not found'}), 404
    owner_id, current_status = owner_row
    role = request._auth.get('role'); auth_user_id = request._auth.get('sub')
    if role != 'admin' and owner_id != auth_user_id:
        cur.close(); conn.close(); return jsonify({'detail': 'forbidden'}), 403
    # Only allow cancel of active bookings here (even admin); use force-cancel for others
    if current_status != 'active':
        cur.close(); conn.close(); return jsonify({'detail': 'cannot cancel non-active booking'}), 400
    cur.execute("UPDATE bookings SET status = 'canceled', updated_at = NOW() WHERE id = %s RETURNING id, user_id, room_id, start_time, end_time, status", (booking_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'detail': 'booking not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row))

@app.post('/bookings/<int:booking_id>/force-cancel')
@require_roles('admin')
def force_cancel_booking(booking_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'canceled', updated_at = NOW() WHERE id = %s RETURNING id, user_id, room_id, start_time, end_time, status", (booking_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'detail': 'booking not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row))

@app.get('/bookings/check')
def check_availability():
    room_id = request.args.get('room_id')
    start = request.args.get('start')
    end = request.args.get('end')


    if not (room_id and start and end):
        return jsonify({'detail': 'room_id, start, end required'}), 400
    try:
        room_id_int = int(room_id)
    except Exception:
        return jsonify({'detail': 'room_id must be integer'}), 400
    

    start_dt = _to_naive(_parse_iso(start)); end_dt = _to_naive(_parse_iso(end))
    if not start_dt or not end_dt or end_dt <= start_dt:
        return jsonify({'detail': 'invalid times'}), 400
    

    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id FROM rooms WHERE id = %s', (room_id_int,))
    if not cur.fetchone():
        cur.close(); conn.close(); return jsonify({'detail': 'room not found'}), 404
    cur.execute('''SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active'
                   AND start_time < %s AND end_time > %s LIMIT 1''', (room_id_int, end_dt, start_dt))
    conflict = cur.fetchone() is not None
    cur.close(); conn.close()
    return jsonify({'room_id': room_id_int, 'available': not conflict})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8003))
    app.run(host='0.0.0.0', port=port)