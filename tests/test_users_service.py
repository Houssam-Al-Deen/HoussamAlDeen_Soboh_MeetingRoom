import os
import sys
import json
import time
import pytest
import psycopg2

# Ensure project root is on path for 'shared' and 'services' imports
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Explicit test env vars (local host Postgres via docker compose port mapping)
os.environ.setdefault('POSTGRES_USER', 'smr')
os.environ.setdefault('POSTGRES_PASSWORD', 'smr_pass')
os.environ.setdefault('POSTGRES_DB', 'smart_meeting_room')
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5433'

from shared.db import get_conn

def _wait_for_db(retries=30, delay=0.5):
    last_err = None
    for _ in range(retries):
        try:
            conn = get_conn(); conn.close(); return
        except Exception as e:
            last_err = e
            time.sleep(delay)
    raise RuntimeError(f'Database not reachable for tests after {retries*delay:.1f}s: {last_err}')

_wait_for_db()

from services.users_service.app import app  # app already initializes tables


@pytest.fixture(autouse=True)
def clean_db():
    """Truncate tables before each test to keep isolation."""
    conn = get_conn(); cur = conn.cursor()
    # Order: child tables first then users
    cur.execute("TRUNCATE reviews RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE bookings RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE rooms RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
    conn.commit(); cur.close(); conn.close()
    yield


@pytest.fixture
def client():
    return app.test_client()


def register(client, username="u1", email="u1@example.com", password="Pass123!"):
    resp = client.post('/users/register', json={
        'username': username,
        'email': email,
        'password': password,
        'full_name': 'User One'
    })
    return resp


def login(client, username="u1", password="Pass123!"):
    return client.post('/auth/login', json={'username': username, 'password': password})


def test_register_success(client):
    r = register(client)
    assert r.status_code == 201
    data = r.get_json()
    assert data['username'] == 'u1'
    assert 'id' in data


def test_register_duplicate_username(client):
    assert register(client).status_code == 201
    r2 = register(client)
    assert r2.status_code == 409  # conflict


def test_login_success(client):
    register(client)
    r = login(client)
    assert r.status_code == 200
    token = r.get_json().get('access_token')
    assert token


def test_login_invalid_password(client):
    register(client)
    r = login(client, password="WrongPass")
    assert r.status_code == 401


def test_get_me_after_login(client):
    register(client)
    token = login(client).get_json()['access_token']
    r = client.get('/users/me', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    assert r.get_json()['username'] == 'u1'


def test_update_me_email_and_name(client):
    register(client)
    token = login(client).get_json()['access_token']
    r = client.patch('/users/me', json={'email': 'u1_new@example.com', 'full_name': 'Changed Name'}, headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 200
    data = r.get_json()
    assert data['email'] == 'u1_new@example.com'
    assert data['full_name'] == 'Changed Name'


def test_delete_me(client):
    register(client)
    token = login(client).get_json()['access_token']
    r = client.delete('/users/me', headers={'Authorization': f'Bearer {token}'})
    assert r.status_code == 204
    # second attempt should 401 or 404
    r2 = client.get('/users/me', headers={'Authorization': f'Bearer {token}'})
    assert r2.status_code in (401, 404)
