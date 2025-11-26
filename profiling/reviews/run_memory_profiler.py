import os
import time
import jwt
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from memory_profiler import profile

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from services.reviews_service.app import app as reviews_app  # noqa
from shared.db import get_conn

JWT_SECRET = os.getenv('JWT_SECRET', 'devsecret')
API_PREFIX = f"/api/{os.getenv('API_VERSION','v1')}"


def make_token(user_id: int, username: str, role: str) -> str:
    return jwt.encode({'sub': user_id, 'username': username, 'role': role, 'exp': int(time.time()) + 3600}, JWT_SECRET, algorithm='HS256')


def bootstrap():
    conn = get_conn(); cur = conn.cursor()
    for uname, role in [('admin','admin'), ('u1','user'), ('mod','moderator')]:
        cur.execute('SELECT id FROM users WHERE username=%s', (uname,))
        if not cur.fetchone():
            cur.execute('INSERT INTO users (username,email,full_name,role,password_hash) VALUES (%s,%s,%s,%s,%s)', (uname, f'{uname}@example.com', uname.title(), role, 'x'))
    cur.execute("SELECT id FROM rooms WHERE name='RREV'")
    if not cur.fetchone():
        cur.execute("INSERT INTO rooms (name,capacity,equipment,location) VALUES ('RREV',3,'Board','L3')")
    conn.commit(); cur.close(); conn.close()

@profile
def main():
    bootstrap()
    client = reviews_app.test_client()
    admin_tok = make_token(1, 'admin', 'admin')
    user_tok = make_token(2, 'u1', 'user')
    mod_tok = make_token(3, 'mod', 'moderator')
    h_user = {'Authorization': f'Bearer {user_tok}'}
    h_mod = {'Authorization': f'Bearer {mod_tok}'}
    h_admin = {'Authorization': f'Bearer {admin_tok}'}
    client.post(f'{API_PREFIX}/reviews', json={'room_id': 1, 'user_id': 2, 'rating': 5, 'comment': 'Nice'}, headers=h_user)
    client.get(f'{API_PREFIX}/rooms/1/reviews')
    client.patch(f'{API_PREFIX}/reviews/1', json={'comment': 'Updated'}, headers=h_user)
    client.post(f'{API_PREFIX}/reviews/1/flag', json={'reason': 'spam'}, headers=h_mod)
    client.delete(f'{API_PREFIX}/reviews/1', headers=h_admin)


if __name__ == '__main__':
    main()
