"""Reviews service
-------------------

Endpoints to create, list, update, flag, and delete reviews. Uses
JWT auth with simple role checks.
"""

import os
import jwt
import functools
from flask import Flask, request, jsonify
from datetime import datetime, timezone

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
# Skip DB initialization when building docs to avoid side effects
if os.getenv('DOCS_BUILD') != '1':
    init_tables()  # reviews table exists in shared schema
install_error_handlers(app)

JWT_SECRET = os.getenv("JWT_SECRET", "devsecret")
API_PREFIX = f"/api/{os.getenv('API_VERSION', 'v1')}"

def _decode_token():
    """Decode and validate the JWT from the ``Authorization`` header.

    :returns: ``(payload, error)``; error is ``None`` on success or a tuple
              ``(message, status_code)`` describing the failure.
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
    """Decorator that enforces JWT authentication for a route.

    On success, puts the payload in ``request._auth``.

    :param fn: Route function.
    :returns: Wrapped function.
    """
    @functools.wraps(fn)
    def inner(*a, **kw):
        info = _decode_token()
        request._auth = info
        return fn(*a, **kw)
    return inner

def require_roles(*roles):
    """Decorator that enforces role membership.

    :param roles: Allowed roles (e.g., ``'admin'``, ``'moderator'``).
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

def _norm_dt(dt):
    """Normalize datetimes to naive ISO strings for JSON.

    :param dt: ``datetime`` or ``None``.
    :returns: ISO string or ``None``.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()

def _review_row_to_dict(r):
    """Convert a reviews table row tuple to a dict.

    :param r: Database row tuple.
    :returns: Review dictionary.
    """
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

@app.post(f"{API_PREFIX}/reviews")
@rate_limit(60, 60, key='user')
@require_auth
def create_review():
    """Create a new review.

    :request body: JSON with ``room_id`` (int), ``user_id`` (int), ``rating`` (1-5), optional ``comment`` (str).
    :returns: The created review as JSON and a 201 status code.
    :rtype: flask.Response
    :raises 400: Missing fields or invalid rating.
    :raises 401: Missing/invalid token.
    :raises 403: Non-admin creating for another user.
    :raises 404: Referenced room or user not found.
    """
    data = request.get_json() or {}
    room_id = data.get('room_id')
    user_id = data.get('user_id')
    rating = data.get('rating')
    comment = data.get('comment')
    if not (room_id and user_id and rating):
        raise APIError('room_id, user_id, rating required', status=400, code='validation_error')

    role = request._auth.get('role')
    auth_user_id = request._auth.get('sub')
    if role != 'admin' and user_id != auth_user_id:
        raise APIError('forbidden', status=403, code='forbidden')
    try:
        rating = int(rating)
        if rating < 1 or rating > 5:
            raise ValueError
    except Exception:
        raise APIError('rating must be 1..5', status=400, code='validation_error')
    # Inter-service validation before DB insert
    from shared.service_client import ensure_room_exists, ensure_user_exists
    ensure_room_exists(int(room_id))
    ensure_user_exists(int(user_id))
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''INSERT INTO reviews (room_id, user_id, rating, comment)
                   VALUES (%s, %s, %s, %s) RETURNING id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at''',
                (room_id, user_id, rating, comment))
    row = cur.fetchone(); conn.commit(); cur.close(); conn.close()
    return jsonify(_review_row_to_dict(row)), 201

@app.get(f"{API_PREFIX}/rooms/<int:room_id>/reviews")
def list_room_reviews(room_id):
    """List reviews for a specific room.

    :param room_id: Room identifier.
    :type room_id: int
    :returns: JSON array of reviews (most recent first).
    :rtype: flask.Response
    """
    # Ensure room exists via rooms service
    from shared.service_client import ensure_room_exists
    ensure_room_exists(int(room_id))
    conn = get_conn(); cur = conn.cursor()
    cur.execute('''SELECT id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at
                   FROM reviews WHERE room_id = %s ORDER BY created_at DESC''', (room_id,))
    rows = cur.fetchall(); cur.close(); conn.close()
    return jsonify([_review_row_to_dict(r) for r in rows])

@app.patch(f"{API_PREFIX}/reviews/<int:review_id>")
@rate_limit(60, 60, key='user')
@require_auth
def update_review(review_id):
    """Update a review you own (or as moderator/admin).

    Accepted fields: ``rating`` (1-5) and ``comment`` (str).

    :param review_id: Review identifier.
    :type review_id: int
    :returns: The updated review as JSON.
    :rtype: flask.Response

    :raises 400: No valid fields provided or invalid rating.
    :raises 401: Missing/invalid token.
    :raises 403: Not authorized to update this review.
    :raises 404: Review not found.
    """
    data = request.get_json() or {}
    fields = {}
    if 'rating' in data:
        try:
            rt = int(data['rating'])
            if rt < 1 or rt > 5:
                raise ValueError
            fields['rating'] = rt
        except Exception:
            raise APIError('rating must be 1..5', status=400, code='validation_error')
    if 'comment' in data:
        fields['comment'] = data['comment']
    if not fields:
        raise APIError('no updatable fields provided', status=400, code='validation_error')
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at FROM reviews WHERE id = %s', (review_id,))
    existing = cur.fetchone()
    if not existing:
        cur.close(); conn.close(); raise APIError('review not found', status=404, code='not_found')
    owner_id = existing[2]
    role = request._auth.get('role'); auth_user_id = request._auth.get('sub')
    if role not in ('admin', 'moderator') and owner_id != auth_user_id:
        cur.close(); conn.close(); raise APIError('forbidden', status=403, code='forbidden')
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

@app.delete(f"{API_PREFIX}/reviews/<int:review_id>")
@rate_limit(60, 60, key='user')
@require_auth
def delete_review(review_id):
    """Delete a review.

    Allowed for the owner, moderators, and admins.

    :param review_id: Review identifier.
    :type review_id: int
    :returns: JSON with a simple deletion confirmation.
    :rtype: flask.Response

    :raises 401: Missing/invalid token.
    :raises 403: Not authorized to delete this review.
    :raises 404: Review not found.
    """
    conn = get_conn(); cur = conn.cursor()
    cur.execute('SELECT id, user_id FROM reviews WHERE id = %s', (review_id,))
    existing = cur.fetchone()
    if not existing:
        cur.close(); conn.close(); raise APIError('review not found', status=404, code='not_found')
    owner_id = existing[1]
    role = request._auth.get('role'); auth_user_id = request._auth.get('sub')
    if role not in ('admin','moderator') and owner_id != auth_user_id:
        cur.close(); conn.close(); raise APIError('forbidden', status=403, code='forbidden')
    cur.execute('DELETE FROM reviews WHERE id = %s RETURNING id', (review_id,))
    row = cur.fetchone()
    conn.commit(); cur.close(); conn.close()
    return jsonify({'detail': 'deleted', 'id': row[0]})

@app.post(f"{API_PREFIX}/reviews/<int:review_id>/flag")
@rate_limit(60, 60, key='user')
@require_roles('admin','moderator')
def flag_review(review_id):
    """Flag a review (moderator/admin).

    :param review_id: Review identifier.
    :type review_id: int
    :request body: JSON with optional ``reason`` (str).
    :returns: The flagged review as JSON.
    :rtype: flask.Response

    :raises 401: Missing/invalid token.
    :raises 403: Caller is not admin/moderator.
    :raises 404: Review not found.
    """
    data = request.get_json() or {}
    reason = data.get('reason')
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE reviews SET is_flagged = TRUE, flag_reason = %s, updated_at = NOW() WHERE id = %s RETURNING id, room_id, user_id, rating, comment, is_flagged, flag_reason, created_at, updated_at", (reason, review_id))
    row = cur.fetchone()
    if not row:
        conn.rollback(); cur.close(); conn.close(); raise APIError('review not found', status=404, code='not_found')
    conn.commit(); cur.close(); conn.close()
    return jsonify(_review_row_to_dict(row))

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8004))
    app.run(host='0.0.0.0', port=port)
