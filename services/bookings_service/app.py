import os
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

def _booking_row_to_dict(r):
    return {
        'id': r[0],
        'user_id': r[1],
        'room_id': r[2],
        'start_time': r[3].isoformat(),
        'end_time': r[4].isoformat(),
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
def create_booking():
    data = request.get_json() or {}
    user_id = data.get('user_id')
    room_id = data.get('room_id')
    start = data.get('start_time')
    end = data.get('end_time')


    if not (user_id and room_id and start and end):
        return jsonify({'detail': 'user_id, room_id, start_time, end_time required'}), 400
    
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
def list_bookings():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''SELECT b.id, b.user_id, b.room_id, b.start_time, b.end_time, b.status,
                   u.username, r.name
                   FROM bookings b
                   JOIN users u ON b.user_id = u.id
                   JOIN rooms r ON b.room_id = r.id
                   ORDER BY b.start_time ASC''')
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
def update_booking(booking_id):
    data = request.get_json() or {}
    new_room_id = data.get('room_id')
    start = data.get('start_time')
    end = data.get('end_time')


    if not (new_room_id or start or end):
        return jsonify({'detail': 'no fields to update'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, user_id, room_id, start_time, end_time, status FROM bookings WHERE id = %s', (booking_id,))
    existing = cur.fetchone()


    if not existing:
        cur.close(); conn.close(); return jsonify({'detail': 'booking not found'}), 404
    

    if existing[5] != 'active':
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
    
    # Conflict check excluding this booking
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
def cancel_booking(booking_id):
    # Soft cancel: set status = 'canceled'
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