import os
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
init_tables()  # reviews table exists in shared schema

JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")

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

def _norm_dt(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()

def _review_row_to_dict(r):
    return {
        'id': r[0],
        'room_id': r[1],
        'user_id': r[2],
        'rating': r[3],
        'comment': r[4],
        'is_flagged': r[5],
        'flag_reason': r[6],
        'created_at': _norm_dt(r[7]),
        'updated_at': _norm_dt(r[8])
    }

@app.post('/reviews')
@require_auth
def create_review():
    data = request.get_json() or {}
    room_id = data.get('room_id')
    user_id = data.get('user_id')
    rating = data.get('rating')
    comment = data.get('comment')
    if not (room_id and user_id and rating):
        return jsonify({'detail': 'room_id, user_id, rating required'}), 400

    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role != 'admin' and user_id != auth_user_id:
        return jsonify({'detail': 'forbidden'}), 403
    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except Exception:
        return jsonify({'detail': 'rating must be 1..5'}), 400
    conn = get_conn(); cur = conn.cursor()
    # check room & user
    cur.execute('SELECT id FROM rooms WHERE id = %s', (room_id,))
    if not cur.fetchone():
        cur.close(); conn.close(); return jsonify({'detail': 'room not found'}), 404
    cur.execute('SELECT id FROM users WHERE id = %s', (user_id,))
    if not cur.fetchone():
        cur.close(); conn.close(); return jsonify({'detail': 'user not found'}), 404
    cur.execute('''INSERT INTO reviews (room_id, user_id, rating, comment)
                   VALUES (%s, %s, %s, %s) RETURNING id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at''',
                (room_id, user_id, rating, comment))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(_review_row_to_dict(row)), 201

@app.get('/rooms/<int:room_id>/reviews')
def list_room_reviews(room_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''SELECT id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at
                   FROM reviews WHERE room_id = %s ORDER BY created_at DESC''', (room_id,))
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([_review_row_to_dict(r) for r in rows])

@app.patch('/reviews/<int:review_id>')
@require_auth
def update_review(review_id):
    data = request.get_json() or {}
    fields = {}
    if 'rating' in data:
        try:
            rt = int(data['rating'])
            if rt < 1 or rt > 5:
                raise ValueError
            fields['rating'] = rt
        except Exception:
            return jsonify({'detail': 'rating must be 1..5'}), 400
    if 'comment' in data:
        fields['comment'] = data['comment']
    if not fields:
        return jsonify({'detail': 'no updatable fields provided'}), 400
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at FROM reviews WHERE id = %s', (review_id,))
    existing = cur.fetchone()
    if not existing:
        cur.close(); conn.close(); return jsonify({'detail': 'review not found'}), 404
    owner_id = existing[2]
    role = request._auth.get('role'); auth_user_id = request._auth.get('sub')
    if role not in ('admin', 'moderator') and owner_id != auth_user_id:
        cur.close(); conn.close(); return jsonify({'detail': 'forbidden'}), 403
    sets = []
    params = []
    for k,v in fields.items():
        sets.append(f"{k} = %s")
        params.append(v)
    params.append(review_id)
    cur.execute(f"UPDATE reviews SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s RETURNING id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at", params)
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return jsonify(_review_row_to_dict(row))

@app.delete('/reviews/<int:review_id>')
@require_auth
def delete_review(review_id):
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, user_id FROM reviews WHERE id = %s', (review_id,))
    existing = cur.fetchone()
    if not existing:
        cur.close(); conn.close(); return jsonify({'detail': 'review not found'}), 404
    owner_id = existing[1]
    role = request._auth.get('role'); auth_user_id = request._auth.get('sub')
    if role not in ('admin','moderator') and owner_id != auth_user_id:
        cur.close(); conn.close(); return jsonify({'detail': 'forbidden'}), 403
    cur.execute('DELETE FROM reviews WHERE id = %s RETURNING id', (review_id,))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return jsonify({'detail': 'deleted', 'id': row[0]})

@app.post('/reviews/<int:review_id>/flag')
@require_roles('admin','moderator')
def flag_review(review_id):
    data = request.get_json() or {}
    reason = data.get('reason')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE reviews SET is_flagged = TRUE, flag_reason = %s, updated_at = NOW() WHERE id = %s RETURNING id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at", (reason, review_id))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); return jsonify({'detail': 'review not found'}), 404
    conn.commit(); cur.close(); conn.close()
    return jsonify(_review_row_to_dict(row))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8004))
    app.run(host='0.0.0.0', port=port)
