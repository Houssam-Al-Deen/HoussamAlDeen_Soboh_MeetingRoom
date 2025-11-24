import os
import sys
import time
import pytest

# Project root on path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# DB env (docker compose mapping)
os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from shared.db import get_conn
from services.users_service.app import app

# Wait for DB readiness briefly
for _ in range(30):
    try:
        c = get_conn(); c.close(); break
    except Exception:
        time.sleep(0.3)

@pytest.fixture(autouse=True)
def clean_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute('TRUNCATE reviews RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE bookings RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE rooms RESTART IDENTITY CASCADE')
    cur.execute('TRUNCATE users RESTART IDENTITY CASCADE')
    conn.commit(); cur.close(); conn.close()
    yield

@pytest.fixture
def client():
    return app.test_client()


def register(client, username='u1', email='u1@example.com', password='Pass123!', role=None):
    payload = {
        'username': username,
        'email': email,
        'password': password,
        'full_name': 'Full Name'
    }
    if role:
        payload['role'] = role
    return client.post('/users/register', json=payload)

def login(client, username='u1', password='Pass123!'):
    return client.post('/auth/login', json={'username': username, 'password': password})

def auth_header(token):
    return {'Authorization': f'Bearer {token}'}

def create_basic_user_and_token(client, username='u1'):
    assert register(client, username=username, email=f'{username}@example.com').status_code == 201
    tok = login(client, username=username).get_json()['access_token']
    return tok


def test_bootstrap_admin_registration(client):
    r = register(client, username='admin', email='admin@example.com', password='AdminPass123', role='admin')
    assert r.status_code == 201
    assert r.get_json()['role'] == 'admin'

def test_admin_creates_user_and_list_users(client):
    # Bootstrap admin (no admin exists yet, so allowed without token)
    r_admin = register(client, username='admin', email='admin@example.com', password='AdminPass123', role='admin')
    assert r_admin.status_code == 201
    admin_token = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    # Create a normal user (self signup allowed)
    assert register(client, 'alice', 'alice@example.com').status_code == 201
    # List users with admin token
    r_list = client.get('/users', headers=auth_header(admin_token))
    assert r_list.status_code == 200
    usernames = [u['username'] for u in r_list.get_json()]
    assert set(['admin', 'alice']).issubset(set(usernames))

def test_register_moderator_with_admin_token(client):
    # bootstrap admin
    assert register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin').status_code == 201
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.post('/users/register', json={
        'username': 'mod1', 'email': 'mod1@example.com', 'password': 'ModPass123', 'role': 'moderator'
    }, headers=auth_header(admin_tok))
    assert r.status_code == 201 and r.get_json()['role'] == 'moderator'

def test_login_success(client):
    register(client)
    r = login(client)
    assert r.status_code == 200 and 'access_token' in r.get_json()

def test_get_me_success(client):
    tok = create_basic_user_and_token(client)
    r = client.get('/users/me', headers=auth_header(tok))
    assert r.status_code == 200 and r.get_json()['username'] == 'u1'

def test_update_me_multiple_fields(client):
    tok = create_basic_user_and_token(client)
    r = client.patch('/users/me', json={'email': 'u1_new@example.com', 'full_name': 'Changed', 'password': 'NewPass456'}, headers=auth_header(tok))
    assert r.status_code == 200
    body = r.get_json()
    assert body['email'] == 'u1_new@example.com' and body['full_name'] == 'Changed'

def test_delete_me_success(client):
    tok = create_basic_user_and_token(client)
    r = client.delete('/users/me', headers=auth_header(tok))
    assert r.status_code == 204
    r2 = client.get('/users/me', headers=auth_header(tok))
    assert r2.status_code == 404

def test_get_user_by_username_self_and_admin(client):
    # create admin
    assert register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin').status_code == 201
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    # create normal user
    assert register(client, 'alice', 'alice@example.com').status_code == 201
    user_tok = login(client, 'alice').get_json()['access_token']
    # self access
    r_self = client.get('/users/alice', headers=auth_header(user_tok))
    assert r_self.status_code == 200
    # admin access
    r_admin = client.get('/users/alice', headers=auth_header(admin_tok))
    assert r_admin.status_code == 200

def test_user_booking_history_happy(client):
    # Need user, room and booking rows inserted manually
    assert register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin').status_code == 201
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    assert register(client, 'bob', 'bob@example.com').status_code == 201
    bob_tok = login(client, 'bob').get_json()['access_token']
    # Insert room and booking directly
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT INTO rooms (name, capacity) VALUES ('R1', 5) RETURNING id")
    room_id = cur.fetchone()[0]
    cur.execute("INSERT INTO bookings (user_id, room_id, start_time, end_time) VALUES ( (SELECT id FROM users WHERE username='bob'), %s, NOW() - INTERVAL '1 hour', NOW() )", (room_id,))
    conn.commit(); cur.close(); conn.close()
    r_self = client.get('/users/bob/bookings', headers=auth_header(bob_tok))
    assert r_self.status_code == 200 and len(r_self.get_json()) == 1
    r_admin = client.get('/users/bob/bookings', headers=auth_header(admin_tok))
    assert r_admin.status_code == 200 and len(r_admin.get_json()) == 1

def test_admin_update_and_delete_user(client):
    # bootstrap admin
    assert register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin').status_code == 201
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    assert register(client, 'charlie', 'charlie@example.com').status_code == 201
    # update charlie role
    r_upd = client.patch('/users/charlie', json={'role': 'moderator'}, headers=auth_header(admin_tok))
    assert r_upd.status_code == 200 and r_upd.get_json()['role'] == 'moderator'
    # delete charlie
    r_del = client.delete('/users/charlie', headers=auth_header(admin_tok))
    assert r_del.status_code == 200
    # confirm gone
    r_get = client.get('/users/charlie', headers=auth_header(admin_tok))
    assert r_get.status_code == 404



def test_register_invalid_role(client):
    r = register(client, username='bad', email='bad@example.com', role='superhero')
    assert r.status_code == 400

def test_register_duplicate_email_or_username(client):
    assert register(client, 'u1', 'u1@example.com').status_code == 201
    r_dup_user = register(client, 'u1', 'new@example.com')
    assert r_dup_user.status_code == 409
    r_dup_email = register(client, 'u2', 'u1@example.com')
    assert r_dup_email.status_code == 409

def test_register_admin_requires_token_after_bootstrap(client):
    assert register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin').status_code == 201
    r = register(client, 'admin2', 'admin2@example.com', 'AdminPass123', role='admin')
    assert r.status_code == 403

def test_register_moderator_without_admin_token(client):
    # need an existing admin first
    assert register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin').status_code == 201
    r = register(client, 'mod1', 'mod1@example.com', 'ModPass123', role='moderator')
    assert r.status_code == 403

def test_login_invalid_credentials(client):
    register(client)
    r = login(client, password='WrongPass')
    assert r.status_code == 401
    r2 = client.post('/auth/login', json={'username': 'nouser', 'password': 'x'})
    assert r2.status_code == 401

def test_list_users_forbidden_non_admin(client):
    register(client, 'u1', 'u1@example.com')
    tok = login(client, 'u1').get_json()['access_token']
    r = client.get('/users', headers=auth_header(tok))
    assert r.status_code == 403

def test_get_me_requires_auth(client):
    r = client.get('/users/me')
    assert r.status_code == 401

def test_update_me_no_fields(client):
    tok = create_basic_user_and_token(client)
    r = client.patch('/users/me', json={}, headers=auth_header(tok))
    assert r.status_code == 400

def test_update_me_duplicate_email(client):
    register(client, 'u1', 'u1@example.com')
    register(client, 'u2', 'u2@example.com')
    tok1 = login(client, 'u1').get_json()['access_token']
    r = client.patch('/users/me', json={'email': 'u2@example.com'}, headers=auth_header(tok1))
    assert r.status_code == 409

def test_get_user_forbidden_other_user(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'alice', 'alice@example.com')
    register(client, 'bob', 'bob@example.com')
    alice_tok = login(client, 'alice').get_json()['access_token']
    r = client.get('/users/bob', headers=auth_header(alice_tok))
    assert r.status_code == 403

def test_user_booking_history_forbidden_other_user(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'alice', 'alice@example.com')
    register(client, 'bob', 'bob@example.com')
    alice_tok = login(client, 'alice').get_json()['access_token']
    r = client.get('/users/bob/bookings', headers=auth_header(alice_tok))
    assert r.status_code == 403

def test_admin_update_user_invalid_role(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'alice', 'alice@example.com')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.patch('/users/alice', json={'role': 'invalid'}, headers=auth_header(admin_tok))
    assert r.status_code == 400

def test_admin_update_user_duplicate_email(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'alice', 'alice@example.com')
    register(client, 'bob', 'bob@example.com')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.patch('/users/bob', json={'email': 'alice@example.com'}, headers=auth_header(admin_tok))
    assert r.status_code == 409

def test_admin_delete_user_not_found(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.delete('/users/doesnotexist', headers=auth_header(admin_tok))
    assert r.status_code == 404

def test_access_with_invalid_token(client):
    register(client)
    r = client.get('/users/me', headers={'Authorization': 'Bearer badtoken'})
    assert r.status_code == 401



def test_update_me_password_only(client):
    register(client, 'alice', 'alice@example.com')
    tok_old = login(client, 'alice').get_json()['access_token']
    r = client.patch('/users/me', json={'password': 'NewPass999'}, headers=auth_header(tok_old))
    assert r.status_code == 200
    # old password should fail
    r_old_login = login(client, 'alice', 'Pass123!')
    assert r_old_login.status_code == 401
    # new password works
    r_new_login = login(client, 'alice', 'NewPass999')
    assert r_new_login.status_code == 200

def test_admin_update_user_email_only(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    register(client, 'alice', 'alice@example.com')
    r = client.patch('/users/alice', json={'email': 'alice_new@example.com'}, headers=auth_header(admin_tok))
    assert r.status_code == 200 and r.get_json()['email'] == 'alice_new@example.com'

def test_admin_update_user_password_only(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    register(client, 'bob', 'bob@example.com')
    r = client.patch('/users/bob', json={'password': 'BobNewPass123'}, headers=auth_header(admin_tok))
    assert r.status_code == 200
    # Login with new password
    r_login = login(client, 'bob', 'BobNewPass123')
    assert r_login.status_code == 200

def test_admin_update_user_not_found(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.patch('/users/ghost', json={'full_name': 'Ghost'}, headers=auth_header(admin_tok))
    assert r.status_code == 404

def test_admin_get_nonexistent_user(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.get('/users/nobody', headers=auth_header(admin_tok))
    assert r.status_code == 404

def test_admin_get_nonexistent_user_booking_history(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.get('/users/nobody/bookings', headers=auth_header(admin_tok))
    assert r.status_code == 404



def test_register_missing_fields(client):
    r1 = client.post('/users/register', json={'email': 'x@example.com', 'password': 'Pass123!'})  # missing username
    assert r1.status_code == 400
    r2 = client.post('/users/register', json={'username': 'u', 'password': 'Pass123!'})  # missing email
    assert r2.status_code == 400
    r3 = client.post('/users/register', json={'username': 'u', 'email': 'e@example.com'})  # missing password
    assert r3.status_code == 400
    r4 = client.post('/users/register', json={'username': '', 'email': 'e@example.com', 'password': 'x'})
    assert r4.status_code == 400

def test_privileged_register_with_user_token(client):
    # Create bootstrap admin first so privileged path enforced
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'u1', 'u1@example.com')
    user_tok = login(client, 'u1').get_json()['access_token']
    r = client.post('/users/register', json={'username': 'modX', 'email': 'modx@example.com', 'password': 'ModPass123', 'role': 'moderator'}, headers=auth_header(user_tok))
    assert r.status_code == 403

def test_login_missing_fields(client):
    r1 = client.post('/auth/login', json={'password': 'x'})
    assert r1.status_code == 400
    r2 = client.post('/auth/login', json={'username': 'x'})
    assert r2.status_code == 400

def test_list_users_no_auth_header(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    r = client.get('/users')
    assert r.status_code == 401

def test_admin_update_user_no_fields(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'alice', 'alice@example.com')
    admin_tok = login(client, 'admin', 'AdminPass123').get_json()['access_token']
    r = client.patch('/users/alice', json={}, headers=auth_header(admin_tok))
    assert r.status_code == 400

def test_update_me_unchanged_email_only(client):
    register(client, 'alice', 'alice@example.com')
    tok = login(client, 'alice').get_json()['access_token']
    r = client.patch('/users/me', json={'email': 'alice@example.com'}, headers=auth_header(tok))
    assert r.status_code == 400

def test_register_admin_with_user_token_after_bootstrap(client):
    register(client, 'admin', 'admin@example.com', 'AdminPass123', role='admin')
    register(client, 'u1', 'u1@example.com')
    user_tok = login(client, 'u1').get_json()['access_token']
    r = client.post('/users/register', json={'username': 'admin2', 'email': 'admin2@example.com', 'password': 'AdminPass123', 'role': 'admin'}, headers=auth_header(user_tok))
    assert r.status_code == 403
