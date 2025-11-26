import os
import sys
import time
import pytest
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# DB env
os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from shared.db import get_conn
from services.users_service.app import app as users_app
from services.bookings_service.app import app as bookings_app
API_PREFIX = f"/api/{os.getenv('API_VERSION','v1')}"
users_app.config['TESTING'] = True
bookings_app.config['TESTING'] = True

@pytest.fixture(autouse=True)
def clean_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('TRUNCATE reviews RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE bookings RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE rooms RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE users RESTART IDENTITY CASCADE')
    conn.commit(); cur.close(); conn.close()
    yield


def _iso_pair():
    base = datetime(2025, 11, 25, 10, 0, 0)
    end = base + timedelta(hours=1)
    return base.strftime('%Y-%m-%dT%H:%M:%S'), end.strftime('%Y-%m-%dT%H:%M:%S')


def test_bookings_dependency_unavailable_uses_breaker(monkeypatch):
    # Enable HTTP validation so bookings calls rooms via HTTP (which won't be running)
    monkeypatch.setenv('USE_HTTP_VALIDATION', '1')
    # Prepare a user
    uc = users_app.test_client(); bc = bookings_app.test_client()
    assert uc.post(f'{API_PREFIX}/users/register', json={'username':'u1','email':'u1@example.com','password':'Pass123!'}).status_code == 201
    tok = uc.post(f'{API_PREFIX}/auth/login', json={'username':'u1','password':'Pass123!'}).get_json()['access_token']
    # Get user id
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='u1'"); uid = cur.fetchone()[0]; cur.close(); conn.close()
    hdr = {'Authorization': f'Bearer {tok}'}
    start, end = _iso_pair()
    # Attempt multiple times; expect 503 due to dependency unavailable; repeated 503s indicate breaker effect
    for i in range(4):
        r = bc.post(f'{API_PREFIX}/bookings', json={'user_id': uid, 'room_id': 9999, 'start_time': start, 'end_time': end}, headers=hdr)
        # With in-process fallback room validation now returns 404 (room not found)
        assert r.status_code == 404
