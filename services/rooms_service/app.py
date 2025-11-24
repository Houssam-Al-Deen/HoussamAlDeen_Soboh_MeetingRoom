import os
import time
import jwt
import functools
from flask import Flask, request, jsonify

# Import shared DB helpers with fallback path logic
try:
    from shared.db import get_conn, init_tables
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from shared.db import get_conn, init_tables

app = Flask(__name__)
init_tables()  # ensure tables exist

JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
JWT_EXP_SECONDS = 3600

# --------------- Auth helpers (mirrors users service) ---------------

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

# Helper to convert a room row tuple to dict
def _room_row_to_dict(r):
    return {
        'id': r[0],
        'name': r[1],
        'capacity': r[2],
        'equipment': r[3],
        'location': r[4],
        'is_active': r[5]
    }

@app.post('/rooms')
@require_roles('admin')
def create_room():
    """Create a new meeting room (name, capacity, equipment, location)."""
    data = request.get_json() or {}
    name = data.get('name')
    capacity = data.get('capacity')
    if not name or capacity is None:
        return jsonify({'detail': 'name and capacity required'}), 400
    try:
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError
    except Exception:
        return jsonify({'detail': 'capacity must be a positive integer'}), 400
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
            return jsonify({'detail': 'room name already exists'}), 409
        return jsonify({'detail': 'error creating room'}), 500
    cur.close(); conn.close()
    return jsonify(_room_row_to_dict(row)), 201

@app.get('/rooms')
def list_rooms():
    """List all rooms (helper for manual inspection)."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, name, capacity, equipment, location, is_active FROM rooms ORDER BY id ASC")
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([_room_row_to_dict(r) for r in rows])

@app.patch('/rooms/<int:room_id>')
@require_roles('admin')
def update_room(room_id):
    """Update capacity/equipment/location of a room."""
    data = request.get_json() or {}
    fields = {}
    if 'capacity' in data:
        try:
            cap = int(data['capacity'])
            if cap <= 0:
                raise ValueError
            fields['capacity'] = cap
        except Exception:
            return jsonify({'detail': 'capacity must be positive integer'}), 400
    if 'equipment' in data:
        fields['equipment'] = data['equipment']
    if 'location' in data:
        fields['location'] = data['location']
    if not fields:
        return jsonify({'detail': 'no updatable fields provided'}), 400
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
        return jsonify({'detail': 'room not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify(_room_row_to_dict(row))

@app.delete('/rooms/<int:room_id>')
@require_roles('admin')
def delete_room(room_id):
    """Delete a room by id."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM rooms WHERE id = %s RETURNING id", (room_id,))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close()
        return jsonify({'detail': 'room not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify({'detail': 'deleted', 'id': row[0]})

@app.get('/rooms/available')
def available_rooms():
    """Return rooms matching optional filters (capacity, location, equipment tokens)."""
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
            return jsonify({'detail': 'capacity must be integer'}), 400
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

@app.get('/rooms/<int:room_id>/status')
def room_status(room_id):
    """Return current status (available/booked) for a room using active bookings now."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, name FROM rooms WHERE id = %s", (room_id,))
    room_row = cur.fetchone()
    if not room_row:
        cur.close(); conn.close()
        return jsonify({'detail': 'room not found'}), 404
    cur.execute("SELECT 1 FROM bookings WHERE room_id = %s AND status = 'active' AND start_time <= NOW() AND end_time > NOW() LIMIT 1", (room_id,))
    booked = cur.fetchone() is not None
    cur.close(); conn.close()
    return jsonify({'room_id': room_id, 'name': room_row[1], 'status': 'booked' if booked else 'available'})

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8002))
    app.run(host='0.0.0.0', port=port)
