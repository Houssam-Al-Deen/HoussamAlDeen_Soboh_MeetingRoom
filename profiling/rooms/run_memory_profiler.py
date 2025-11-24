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

from services.rooms_service.app import app as rooms_app  # noqa
from shared.db import get_conn

JWT_SECRET = os.getenv('JWT_SECRET', 'devsecret')


def make_token(user_id: int, username: str, role: str) -> str:
    return jwt.encode({'sub': user_id, 'username': username, 'role': role, 'exp': int(time.time()) + 3600}, JWT_SECRET, algorithm='HS256')


def bootstrap_admin():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username,email,full_name,role,password_hash) VALUES ('admin','admin@example.com','Admin','admin','x')")
    conn.commit(); cur.close(); conn.close()

@profile
def main():
    bootstrap_admin()
    client = rooms_app.test_client()
    admin_token = make_token(1, 'admin', 'admin')
    headers_admin = {'Authorization': f'Bearer {admin_token}'}
    client.post('/rooms', json={'name': 'A', 'capacity': 4, 'equipment': 'TV', 'location': 'L1'}, headers=headers_admin)
    client.post('/rooms', json={'name': 'B', 'capacity': 8, 'equipment': 'Board', 'location': 'L2'}, headers=headers_admin)
    client.get('/rooms')
    client.patch('/rooms/1', json={'capacity': 6, 'equipment': 'TV,Camera'}, headers=headers_admin)
    client.get('/rooms/available?capacity=4&equipment=TV')
    client.get('/rooms/1/status')
    client.delete('/rooms/2', headers=headers_admin)


if __name__ == '__main__':
    main()
