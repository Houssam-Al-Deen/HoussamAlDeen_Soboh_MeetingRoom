import os
from flask import Flask, request, jsonify

# Import shared DB helpers with fallback path logic
try:
    from shared.db import get_conn, init_tables
except ModuleNotFoundError:
    import sys
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from shared.db import get_conn, init_tables

app = Flask(__name__)
init_tables()  # rooms table already created in shared schema

@app.post('/rooms')
def create_room():
    data = request.get_json() or {}
    if not data.get('name') or not data.get('capacity'):
        return jsonify({'detail': 'name and capacity required', 'code': 'BAD_REQUEST'}), 400
    try:
        capacity = int(data['capacity'])
        if capacity <= 0:
            raise ValueError
    except Exception:
        return jsonify({'detail': 'capacity must be positive integer', 'code': 'BAD_REQUEST'}), 400
    conn = get_conn(); cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO rooms (name, capacity, equipment, location, is_active) VALUES (%s, %s, %s, %s, %s) RETURNING id, name, capacity, equipment, location, is_active",
            (
                data['name'],
                capacity,
                data.get('equipment'),
                data.get('location'),
                True
            )
        )
        row = cur.fetchone(); conn.commit()
    except Exception as e:
        conn.rollback(); cur.close(); conn.close()
        if 'unique' in str(e).lower():
            return jsonify({'detail': 'room name already exists', 'code': 'CONFLICT'}), 409
        return jsonify({'detail': 'server error', 'code': 'SERVER_ERROR'}), 500
    cur.close(); conn.close()
    return jsonify({
        'id': row[0],
        'name': row[1],
        'capacity': row[2],
        'equipment': row[3],
        'location': row[4],
        'is_active': row[5]
    }), 201

@app.get('/rooms')
def list_rooms():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id, name, capacity, equipment, location, is_active FROM rooms ORDER BY id ASC")
    rows = cur.fetchall(); cur.close(); conn.close()
    out = []
    for r in rows:
        out.append({
            'id': r[0],
            'name': r[1],
            'capacity': r[2],
            'equipment': r[3],
            'location': r[4],
            'is_active': r[5]
        })
    return jsonify(out)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8002))
    app.run(host='0.0.0.0', port=port)
