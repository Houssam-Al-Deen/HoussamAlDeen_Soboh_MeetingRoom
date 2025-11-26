import os
import sys
import time
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Test DB env
os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from shared.db import get_conn
from services.users_service.app import app as users_app
API_PREFIX = f"/api/{os.getenv('API_VERSION','v1')}"
users_app.config['TESTING'] = True
from shared.rate_limit import reset_rate_limiter

@pytest.fixture(autouse=True)
def clean_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('TRUNCATE reviews RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE bookings RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE rooms RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE users RESTART IDENTITY CASCADE')
    conn.commit(); cur.close(); conn.close()
    reset_rate_limiter()
    yield

@pytest.fixture
def client(monkeypatch):
    # Enable rate limiting only for this test module
    monkeypatch.setenv('RATE_LIMIT_ENABLED', '1')
    return users_app.test_client()


def test_register_rate_limited_by_ip(client):
    # 5/min allowed on register; 6th should 429
    for i in range(5):
        r = client.post(f'{API_PREFIX}/users/register', json={
            'username': f'u{i}', 'email': f'u{i}@example.com', 'password': 'Pass123!'
        })
        assert r.status_code == 201
    r6 = client.post(f'{API_PREFIX}/users/register', json={
        'username': 'u6', 'email': 'u6@example.com', 'password': 'Pass123!'
    })
    assert r6.status_code == 429


def test_login_rate_limited_by_ip(client):
    # Prepare a user
    assert client.post(f'{API_PREFIX}/users/register', json={'username': 'alice', 'email': 'alice@example.com', 'password': 'Pass123!'}).status_code == 201
    # 10/min allowed on login; 11th should 429
    for i in range(10):
        r = client.post(f'{API_PREFIX}/auth/login', json={'username': 'alice', 'password': 'Pass123!'})
        assert r.status_code == 200
    r11 = client.post(f'{API_PREFIX}/auth/login', json={'username': 'alice', 'password': 'Pass123!'})
    assert r11.status_code == 429
