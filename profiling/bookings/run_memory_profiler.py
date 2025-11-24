import os
import time
import jwt
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from datetime import datetime, timedelta
from memory_profiler import profile

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from services.bookings_service.app import app as bookings_app  # noqa
from shared.db import get_conn

JWT_SECRET = os.getenv('JWT_SECRET', 'devsecret')


def make_token(user_id: int, username: str, role: str) -> str:
    return jwt.encode({'sub': user_id, 'username': username, 'role': role, 'exp': int(time.time()) + 3600}, JWT_SECRET, algorithm='HS256')


def bootstrap_fixture_data():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE username='admin'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username,email,full_name,role,password_hash) VALUES ('admin','admin@example.com','Admin','admin','x')")
    cur.execute("SELECT id FROM users WHERE username='u1'")
    if not cur.fetchone():
        cur.execute("INSERT INTO users (username,email,full_name,role,password_hash) VALUES ('u1','u1@example.com','User One','user','x')")
    cur.execute("SELECT id FROM rooms WHERE name='R1'")
    if not cur.fetchone():
        cur.execute("INSERT INTO rooms (name,capacity,equipment,location) VALUES ('R1',4,'TV','L1')")
    cur.execute("SELECT id FROM rooms WHERE name='R2'")
    if not cur.fetchone():
        cur.execute("INSERT INTO rooms (name,capacity,equipment,location) VALUES ('R2',6,'Board','L2')")
    conn.commit(); cur.close(); conn.close()

@profile
def main():
    bootstrap_fixture_data()
    client = bookings_app.test_client()
    admin_token = make_token(1, 'admin', 'admin')
    user_token = make_token(2, 'u1', 'user')
    headers_admin = {'Authorization': f'Bearer {admin_token}'}
    headers_user = {'Authorization': f'Bearer {user_token}'}

    base_start = datetime.now().replace(microsecond=0)
    b1_start = base_start + timedelta(hours=1)
    b1_end = b1_start + timedelta(hours=1)
    b2_start = base_start + timedelta(hours=2)
    b2_end = b2_start + timedelta(hours=1)
    b3_start = base_start + timedelta(hours=3)
    b3_end = b3_start + timedelta(hours=1)

    client.post('/bookings', json={'user_id': 2, 'room_id': 1, 'start_time': b1_start.isoformat(), 'end_time': b1_end.isoformat()}, headers=headers_user)
    client.post('/bookings', json={'user_id': 2, 'room_id': 1, 'start_time': b2_start.isoformat(), 'end_time': b2_end.isoformat()}, headers=headers_user)
    client.post('/bookings', json={'user_id': 1, 'room_id': 2, 'start_time': b3_start.isoformat(), 'end_time': b3_end.isoformat()}, headers=headers_admin)
    client.get('/bookings', headers=headers_admin)
    client.get('/bookings', headers=headers_user)
    upd_start = b1_start + timedelta(minutes=15)
    upd_end = upd_start + timedelta(hours=1)
    client.patch('/bookings/1', json={'start_time': upd_start.isoformat(), 'end_time': upd_end.isoformat()}, headers=headers_user)
    force_upd_start = b3_start + timedelta(minutes=10)
    force_upd_end = force_upd_start + timedelta(hours=1)
    client.patch('/bookings/3', json={'start_time': force_upd_start.isoformat(), 'end_time': force_upd_end.isoformat(), 'force': True}, headers=headers_admin)
    client.get(f'/bookings/check?room_id=1&start={b1_start.isoformat()}&end={b1_end.isoformat()}')
    client.delete('/bookings/2', headers=headers_user)
    client.post('/bookings/3/force-cancel', headers=headers_admin)
    client.delete('/bookings/1', headers=headers_user)


if __name__ == '__main__':
    main()
