"""Bookings service
-------------------

Create, list, update, cancel bookings and check availability. Uses JWT auth with RBAC auth.
"""

import os
import time
import jwt
import functools
from flask import Flask, request, jsonify
from datetime import datetime, timezone
import socket
from shared.service_client import ensure_room_exists, ensure_user_exists, get_user_basic, get_room_basic

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

app = Flask(__name__)
_raw_ver = os.getenv('API_VERSION', 'v1').strip('/')
API_PREFIX = f"/api/{_raw_ver}" if not _raw_ver.startswith('api/') else f"/{_raw_ver}"
# Avoid DB init during Sphinx autodoc imports
if os.getenv('DOCS_BUILD') != '1':
    init_tables()  # bookings table already created in shared schema
install_error_handlers(app)

JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
JWT_EXP_SECONDS = 3600


from shared.circuit_breaker import service_breaker  # imported to ensure breaker initialized



def _decode_token():
    """Decode and validate the JWT from the ``Authorization`` header.

    Expects a header of the form ``Bearer <token>``.
    Raises APIError on failure; returns decoded payload on success.

    :returns: ``dict`` payload when valid.
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
    """Decorator that requires a valid JWT.

    On success, the decoded payload is stored in ``request._auth``.

    :param fn: The route function to wrap.
    :returns: The wrapped function enforcing authentication.
    """
    @functools.wraps(fn)
    def inner(*a, **kw):
        info = _decode_token()
        request._auth = info
        return fn(*a, **kw)
    return inner

def require_roles(*roles):
    """Decorator that requires the caller's role to be in ``roles``.

    Also validates the JWT and stores payload in ``request._auth``.

    :param roles: Allowed role names (e.g., ``'admin'``).
    :type roles: str
    :returns: The wrapped function enforcing role-based access.
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

def _booking_row_to_dict(r):
    """Convert a bookings table row tuple to a JSON-serializable dict.

    Datetimes are normalized to naive ISO strings for consistency.

    :param r: Database row tuple.
    :returns: Booking dictionary.
    """
    def _norm(dt):
        if dt is None:
            return None
        # Strip timezone info - database returns timestamps in session timezone
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
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
    """Parse an ISO 8601 timestamp string.

    :param ts: Timestamp string.
    :returns: ``datetime`` on success, otherwise ``None``.
    """
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None

def _to_naive(dt):
    """Return a naive UTC datetime for easier comparisons.

    Converts aware datetimes to UTC and drops tzinfo. Leaves naive
    datetimes unchanged. Returns ``None`` when input is ``None``.

    :param dt: ``datetime`` or ``None``.
    :returns: Naive ``datetime`` or ``None``.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        # Convert any aware datetime to UTC then drop tzinfo for simple comparisons
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# Previous direct HTTP validation helper replaced by shared.service_client utilities.


@app.post(f"{API_PREFIX}/bookings")
@rate_limit(30, 60, key='user')
@require_auth
def create_booking():
    """Create a booking for a room.

    :request body: JSON with ``user_id``, ``room_id``, ``start_time`` (ISO), ``end_time`` (ISO).
    :returns: Created booking JSON.
    :raises 400: Missing fields, invalid times, or end <= start.
    :raises 401: Missing/invalid token.
    :raises 403: Non-admin creating for another user.
    :raises 404: Unknown user or room.
    :raises 409: Time slot conflict.
    :raises 503: Dependency unavailable (rooms service unreachable or circuit open) when HTTP validation is enabled.
    """
    data = request.get_json() or {}
    user_id = data.get('user_id')
    room_id = data.get('room_id')
    start = data.get('start_time')
    end = data.get('end_time')


    if not (user_id and room_id and start and end):
        raise APIError('user_id, room_id, start_time, end_time required', status=400, code='validation_error')

    # Non-admin users can only create bookings for themselves
    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role != 'admin' and user_id != auth_user_id:
        raise APIError('forbidden', status=403, code='forbidden')
    
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if not start_dt or not end_dt or end_dt <= start_dt:
        raise APIError('invalid times (ISO) or end <= start', status=400, code='validation_error')
    
    # Inter-service existence validation (user, then room) before DB mutation
    ensure_user_exists(int(user_id))
    ensure_room_exists(int(room_id))
    conn = get_conn(); cur = conn.cursor()
    # Conflict detection
    cur.execute('''SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active'
                   AND start_time < %s AND end_time > %s LIMIT 1''', (room_id, end_dt, start_dt))
    if cur.fetchone():
        cur.close(); conn.close(); raise APIError('time slot conflict', status=409, code='conflict')
    cur.execute('''INSERT INTO bookings (user_id, room_id, start_time, end_time)
                   VALUES (%s, %s, %s, %s) RETURNING id, user_id, room_id, start_time, end_time, status''',
                (user_id, room_id, start_dt, end_dt))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row)), 201

@app.get(f"{API_PREFIX}/bookings")
@rate_limit(60, 60, key='user')
@require_auth
def list_bookings():
    """List bookings.

    Admins see all; others see their own.

    :raises 401: Missing/invalid token.
    """
    conn = get_conn(); cur = conn.cursor()
    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role == 'admin':
        cur.execute('SELECT id, user_id, room_id, start_time, end_time, status FROM bookings ORDER BY start_time ASC')
    else:
        cur.execute('SELECT id, user_id, room_id, start_time, end_time, status FROM bookings WHERE user_id = %s ORDER BY start_time ASC', (auth_user_id,))
    rows = cur.fetchall(); cur.close(); conn.close()
    out = []
    for r in rows:
        # Enrich via inter-service calls (simple, no caching)
        user_info = get_user_basic(int(r[1]))
        room_info = get_room_basic(int(r[2]))
        out.append({
            'id': r[0],
            'user_id': r[1],
            'room_id': r[2],
            'start_time': r[3].isoformat(),
            'end_time': r[4].isoformat(),
            'status': r[5],
            'username': user_info.get('username'),
            'room_name': room_info.get('name')
        })
    return jsonify(out)

@app.patch(f"{API_PREFIX}/bookings/<int:booking_id>")
@rate_limit(30, 60, key='user')
@require_auth
def update_booking(booking_id):
    """Update booking times/room.

    Accepts ``room_id``, ``start_time``, ``end_time``; admin may set ``force=true`` to override conflicts/state.
    :param booking_id: Booking identifier.
    :type booking_id: int

    :raises 400: Invalid times, non-active without admin force, or no fields.
    :raises 401: Missing/invalid token.
    :raises 403: Caller is not owner/admin.
    :raises 404: Booking or room not found.
    :raises 409: Time slot conflict (without force).
    :raises 503: Dependency unavailable (rooms service unreachable or circuit open) when HTTP validation is enabled.
    """
    data = request.get_json() or {}
    new_room_id = data.get('room_id')
    start = data.get('start_time')
    end = data.get('end_time')
    force = data.get('force')  # admin override flag


    if not (new_room_id or start or end):
        raise APIError('no fields to update', status=400, code='validation_error')
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, user_id, room_id, start_time, end_time, status FROM bookings WHERE id = %s', (booking_id,))
    existing = cur.fetchone()


    if not existing:
        cur.close(); conn.close(); raise APIError('booking not found', status=404, code='not_found')

    owner_id = existing[1]
    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role != 'admin' and owner_id != auth_user_id:
        cur.close(); conn.close(); raise APIError('forbidden', status=403, code='forbidden')

    if existing[5] != 'active' and not (role == 'admin' and force):
        cur.close(); conn.close(); raise APIError('cannot modify non-active booking', status=400, code='invalid_state')
    

    room_id = existing[2]
    start_dt = existing[3]
    end_dt = existing[4]
    
    # Strip timezone from existing timestamps for consistent comparison
    if start_dt and start_dt.tzinfo:
        start_dt = start_dt.replace(tzinfo=None)
    if end_dt and end_dt.tzinfo:
        end_dt = end_dt.replace(tzinfo=None)

    if new_room_id:
        room_id = new_room_id
        ensure_room_exists(int(room_id))
        
    if start:
        st = _parse_iso(start)
        if not st:
            cur.close(); conn.close(); raise APIError('invalid start_time', status=400, code='validation_error')
        # Normalize parsed timestamp to naive (strip tzinfo) for consistent comparisons
        if st.tzinfo is not None:
            st = st.replace(tzinfo=None)
        start_dt = st

    if end:
        et = _parse_iso(end)
        if not et:
            cur.close(); conn.close(); raise APIError('invalid end_time', status=400, code='validation_error')
        # Normalize parsed timestamp to naive (strip tzinfo) for consistent comparisons
        if et.tzinfo is not None:
            et = et.replace(tzinfo=None)
        end_dt = et
    
    if end_dt <= start_dt:
        cur.close(); conn.close(); raise APIError('end_time must be after start_time', status=400, code='validation_error')
    
    # Conflict check excluding this booking (skip if admin force)
    if not (role == 'admin' and force):
        cur.execute('''SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active' AND id <> %s
                       AND start_time < %s AND end_time > %s LIMIT 1''', (room_id, booking_id, end_dt, start_dt))
        if cur.fetchone():
            cur.close(); conn.close(); raise APIError('time slot conflict', status=409, code='conflict')
    cur.execute('''UPDATE bookings SET room_id = %s, start_time = %s, end_time = %s, updated_at = NOW()
                   WHERE id = %s RETURNING id, user_id, room_id, start_time, end_time, status''',
                (room_id, start_dt, end_dt, booking_id))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row))



@app.delete(f"{API_PREFIX}/bookings/<int:booking_id>")
@rate_limit(30, 60, key='user')
@require_auth
def cancel_booking(booking_id):
    """Cancel an active booking (soft cancel).

    :raises 400: Booking is not active.
    :raises 401: Missing/invalid token.
    :raises 403: Caller is not owner/admin.
    :raises 404: Booking not found.
    """
    # Soft cancel: set status = 'canceled'
    conn = get_conn(); cur = conn.cursor()
    # Ownership/admin check
    cur.execute('SELECT user_id, status FROM bookings WHERE id = %s', (booking_id,))
    owner_row = cur.fetchone()
    if not owner_row:
        cur.close(); conn.close(); raise APIError('booking not found', status=404, code='not_found')
    owner_id, current_status = owner_row
    role = request._auth.get('role'); auth_user_id = request._auth.get('sub')
    if role != 'admin' and owner_id != auth_user_id:
        cur.close(); conn.close(); raise APIError('forbidden', status=403, code='forbidden')
    # Only allow cancel of active bookings here (even admin); use force-cancel for others
    if current_status != 'active':
        cur.close(); conn.close(); raise APIError('cannot cancel non-active booking', status=400, code='invalid_state')
    cur.execute("UPDATE bookings SET status = 'canceled', updated_at = NOW() WHERE id = %s RETURNING id, user_id, room_id, start_time, end_time, status", (booking_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); raise APIError('booking not found', status=404, code='not_found')
    conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row))

@app.post(f"{API_PREFIX}/bookings/<int:booking_id>/force-cancel")
@rate_limit(30, 60, key='user')
@require_roles('admin')
def force_cancel_booking(booking_id):
    """Admin-only cancel regardless of current status.

    :raises 401: Missing/invalid token.
    :raises 403: Caller is not admin.
    :raises 404: Booking not found.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE bookings SET status = 'canceled', updated_at = NOW() WHERE id = %s RETURNING id, user_id, room_id, start_time, end_time, status", (booking_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'detail': 'booking not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify(_booking_row_to_dict(row))

@app.get(f"{API_PREFIX}/bookings/check")
@rate_limit(120, 60, key='ip')
def check_availability():
    """Check if a room is available for a time window.

    :query room_id: Room id (int)
    :query start: ISO start time
    :query end: ISO end time
    :returns: JSON with ``available`` boolean.

    :raises 400: Missing/invalid parameters.
    :raises 404: Room not found.
    """
    room_id = request.args.get('room_id')
    start = request.args.get('start')
    end = request.args.get('end')


    if not (room_id and start and end):
        raise APIError('room_id, start, end required', status=400, code='validation_error')
    try:
        room_id_int = int(room_id)
    except Exception:
        raise APIError('room_id must be integer', status=400, code='validation_error')
    

    start_dt = _to_naive(_parse_iso(start)); end_dt = _to_naive(_parse_iso(end))
    if not start_dt or not end_dt or end_dt <= start_dt:
        raise APIError('invalid times', status=400, code='validation_error')
    

    # Validate room existence via rooms service
    ensure_room_exists(int(room_id_int))
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active'
                   AND start_time < %s AND end_time > %s LIMIT 1''', (room_id_int, end_dt, start_dt))
    conflict = cur.fetchone() is not None
    cur.close(); conn.close()
    return jsonify({'room_id': room_id_int, 'available': not conflict})

@app.get(f"{API_PREFIX}/bookings/room/<int:room_id>/active-status")
def room_active_status(room_id):
    """Return current active booking status for a room.

    Indicates whether there exists an active booking overlapping now.

    :param room_id: Room identifier.
    :type room_id: int
    :returns: JSON with ``room_id`` and ``status`` in {``'booked'``, ``'available'``}.
    """
    # Use CURRENT_TIMESTAMP which respects session timezone
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT 1 FROM bookings
        WHERE room_id = %s AND status = 'active'
        AND start_time <= CURRENT_TIMESTAMP AND end_time > CURRENT_TIMESTAMP
        LIMIT 1
    """, (room_id,))
    booked = cur.fetchone() is not None
    cur.close(); conn.close()
    return jsonify({'room_id': room_id, 'status': 'booked' if booked else 'available'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8003))
    app.run(host='0.0.0.0', port=port)
