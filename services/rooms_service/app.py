"""Rooms service
----------------

Room CRUD and availability/status queries with RBAC auth.
"""

import os
import time
import jwt
import functools
from flask import Flask, request, jsonify

# Import shared DB helpers with fallback path logic
try:
    from shared.db import get_conn, init_tables
    from shared.errors import install_error_handlers, APIError
    from shared.rate_limit import rate_limit
    from shared.service_client import get_room_active_status
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from shared.db import get_conn, init_tables
    from shared.errors import install_error_handlers, APIError
    from shared.rate_limit import rate_limit
    from shared.service_client import get_room_active_status

app = Flask(__name__)
_raw_ver = os.getenv('API_VERSION', 'v1').strip('/')
API_PREFIX = f"/api/{_raw_ver}" if not _raw_ver.startswith('api/') else f"/{_raw_ver}"
# Avoid DB side effects when building docs with autodoc
if os.getenv('DOCS_BUILD') != '1':
    init_tables()  # ensure tables exist
install_error_handlers(app)

JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
JWT_EXP_SECONDS = 3600



def _decode_token():
    """Decode and validate the JWT from the ``Authorization`` header.

    :returns: ``(payload, error)``; error is ``None`` on success or a tuple
              ``(message, status_code)`` when invalid/missing.
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
    """Decorator that requires a valid JWT and sets ``request._auth``.

    :param fn: Route function to wrap.
    :returns: Wrapped function with authentication enforcement.
    """
    @functools.wraps(fn)
    def inner(*a, **kw):
        info = _decode_token()
        request._auth = info
        return fn(*a, **kw)
    return inner

def require_roles(*roles):
    """Decorator that requires the caller to have one of ``roles``.

    Also validates JWT and populates ``request._auth``.

    :param roles: Allowed role names.
    :returns: Wrapped function with role checks.
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

# Helper to convert a room row tuple to dict
def _room_row_to_dict(r):
    """Convert a room table row tuple to a dict.

    :param r: Database row tuple.
    :returns: Room dictionary.
    """
    return {
        'id': r[0],
        'name': r[1],
        'capacity': r[2],
        'equipment': r[3],
        'location': r[4],
        'is_active': r[5]
    }

@app.post(f"{API_PREFIX}/rooms")
@rate_limit(30, 60, key='user')
@require_roles('admin')
def create_room():
    """Create a new meeting room.

    :request body: JSON with ``name`` (str), ``capacity`` (int), optional ``equipment`` (str), ``location`` (str).
    :returns: Created room JSON.
    :raises 400: Missing/invalid fields or capacity.
    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is not an admin.
    :raises 409: Duplicate room name.
    :raises 500: Unexpected database error.
    """
    data = request.get_json() or {}
    name = data.get('name')
    capacity = data.get('capacity')
    if not name or capacity is None:
        raise APIError('name and capacity required', status=400, code='validation_error')
    try:
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError
    except Exception:
        raise APIError('capacity must be a positive integer', status=400, code='validation_error')
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO rooms (name, capacity, equipment, location) VALUES (%s, %s, %s, %s) RETURNING id, name, capacity, equipment, location, is_active",
            (name, capacity, data.get('equipment'), data.get('location'))
        )
        row = cur.fetchone(); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        if 'unique' in str(e).lower():
            raise APIError('room name already exists', status=409, code='conflict')
        raise APIError('error creating room', status=500, code='server_error')
    cur.close(); conn.close()
    return jsonify(_room_row_to_dict(row)), 201

@app.get(f"{API_PREFIX}/rooms")
def list_rooms():
    """List all rooms.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, name, capacity, equipment, location, is_active FROM rooms ORDER BY id ASC")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([_room_row_to_dict(r) for r in rows])

@app.patch(f"{API_PREFIX}/rooms/<int:room_id>")
@rate_limit(60, 60, key='user')
@require_roles('admin')
def update_room(room_id):
    """Update capacity, equipment, or location.

    :param room_id: Room identifier.
    :type room_id: int

    :raises 400: No valid fields or invalid capacity.
    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is not an admin.
    :raises 404: Room not found.
    """
    data = request.get_json() or {}
    fields = {}
    if 'capacity' in data:
        try:
            cap = int(data['capacity'])
            if cap <= 0:
                raise ValueError
            fields['capacity'] = cap
        except Exception:
            raise APIError('capacity must be positive integer', status=400, code='validation_error')
    if 'equipment' in data:
        fields['equipment'] = data['equipment']
    if 'location' in data:
        fields['location'] = data['location']
    if not fields:
        raise APIError('no updatable fields provided', status=400, code='validation_error')
    sets = []
    params = []
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        params.append(v)
    params.append(room_id)
    conn = get_conn(); cur = conn.cursor()
    cur.execute(f"UPDATE rooms SET {', '.join(sets)} WHERE id = %s RETURNING id, name, capacity, equipment, location, is_active", params)
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close()
        raise APIError('room not found', status=404, code='not_found')
    conn.commit(); cur.close(); conn.close()
    return jsonify(_room_row_to_dict(row))

@app.delete(f"{API_PREFIX}/rooms/<int:room_id>")
@rate_limit(60, 60, key='user')
@require_roles('admin')
def delete_room(room_id):
    """Delete a room by id.

    :raises 401: Missing/invalid token.
    :raises 403: Authenticated user is not an admin.
    :raises 404: Room not found.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM rooms WHERE id = %s RETURNING id", (room_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close()
        raise APIError('room not found', status=404, code='not_found')
    conn.commit(); cur.close(); conn.close()
    return jsonify({'detail': 'deleted', 'id': row[0]})

@app.get(f"{API_PREFIX}/rooms/available")
def available_rooms():
    """Filter rooms by capacity, location, and equipment.

    :query capacity: Minimum capacity (int).
    :query location: Substring match for location.
    :query equipment: Comma-separated tokens to search in equipment text.

    :raises 400: Invalid capacity parameter.
    """
    min_capacity = request.args.get('capacity')
    location = request.args.get('location')
    equipment_param = request.args.get('equipment')  # comma separated
    filters = ["is_active = TRUE"]
    params = []
    if min_capacity:
        try:
            filters.append("capacity >= %s")
            params.append(int(min_capacity))
        except Exception:
            raise APIError('capacity must be integer', status=400, code='validation_error')
    if location:
        filters.append("location ILIKE %s")
        params.append(f"%{location}%")
    if equipment_param:
        for token in [t.strip() for t in equipment_param.split(',') if t.strip()]:
            filters.append("equipment ILIKE %s")
            params.append(f"%{token}%")
    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    sql = f"SELECT id, name, capacity, equipment, location, is_active FROM rooms {where_clause} ORDER BY id ASC"
    conn = get_conn(); cur = conn.cursor(); cur.execute(sql, tuple(params)); rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([_room_row_to_dict(r) for r in rows])

@app.get(f"{API_PREFIX}/rooms/<int:room_id>/status")
def room_status(room_id):
    """Return current status (available/booked) for a room.

    Uses active bookings overlapping now.

    :raises 404: Room not found.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM rooms WHERE id = %s", (room_id,))
    room_row = cur.fetchone()
    if not room_row:
        cur.close(); conn.close()
        raise APIError('room not found', status=404, code='not_found')
    cur.close(); conn.close()
    # Delegate booking status to bookings service
    status_info = get_room_active_status(int(room_id))
    return jsonify({'room_id': room_id, 'name': room_row[1], 'status': status_info.get('status', 'unknown')})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8002))
    app.run(host='0.0.0.0', port=port)
