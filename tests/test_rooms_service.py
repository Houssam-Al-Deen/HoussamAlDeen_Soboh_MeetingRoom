import os
import sys
import time
import pytest

# Put project root on path (so imports work when running tests directly)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Basic DB settings (match docker-compose exposed port)
os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from shared.db import get_conn
from services.rooms_service.app import app
from services.users_service.app import app as users_app

# Small wait loop so tests don't fail if DB still starting
def _wait_db():
    for _ in range(20):
        try:
            c = get_conn(); c.close(); return
        except Exception:
            time.sleep(0.3)
_wait_db()

@pytest.fixture(autouse=True)
def reset_tables():
    conn = get_conn(); cur = conn.cursor()
    # Clear related tables to keep references clean
    cur.execute("TRUNCATE reviews RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE bookings RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE rooms RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
    conn.commit(); cur.close(); conn.close()
    yield

@pytest.fixture
def client():
    return app.test_client()

def _auth_header(token):
    return {'Authorization': f'Bearer {token}'}

def create_admin_and_get_token():
    c = users_app.test_client()
    # bootstrap admin
    r = c.post('/users/register', json={
        'username': 'admin', 'email': 'admin@example.com', 'password': 'AdminPass123', 'role': 'admin'
    })
    assert r.status_code == 201
    login_r = c.post('/auth/login', json={'username': 'admin', 'password': 'AdminPass123'})
    return login_r.get_json()['access_token']

def create_room(client, token, name="RoomA", capacity=5, equipment="TV, HDMI", location="Floor 1"):
    return client.post('/rooms', json={
        'name': name,
        'capacity': capacity,
        'equipment': equipment,
        'location': location
    }, headers=_auth_header(token))

def test_create_room(client):
    token = create_admin_and_get_token()
    r = create_room(client, token)
    assert r.status_code == 201
    body = r.get_json()
    assert body['name'] == 'RoomA'
    assert body['capacity'] == 5

def test_duplicate_room_name(client):
    token = create_admin_and_get_token()
    assert create_room(client, token).status_code == 201
    r2 = create_room(client, token)
    assert r2.status_code == 409

def test_list_rooms(client):
    token = create_admin_and_get_token()
    create_room(client, token, name='R1')
    create_room(client, token, name='R2')
    r = client.get('/rooms')
    assert r.status_code == 200
    names = [x['name'] for x in r.get_json()]
    assert 'R1' in names and 'R2' in names

def test_update_room(client):
    token = create_admin_and_get_token()
    room_id = create_room(client, token).get_json()['id']
    r = client.patch(f'/rooms/{room_id}', json={'capacity': 10, 'equipment': 'Projector'}, headers=_auth_header(token))
    assert r.status_code == 200
    body = r.get_json()
    assert body['capacity'] == 10
    assert 'Projector' in body['equipment']

def test_delete_room(client):
    token = create_admin_and_get_token()
    room_id = create_room(client, token).get_json()['id']
    r = client.delete(f'/rooms/{room_id}', headers=_auth_header(token))
    assert r.status_code == 200
    # Should no longer appear in list
    lst = client.get('/rooms').get_json()
    ids = [x['id'] for x in lst]
    assert room_id not in ids

def test_available_rooms_filters(client):
    token = create_admin_and_get_token()
    create_room(client, token, name='Small', capacity=4, equipment='TV, Whiteboard', location='Floor 3')
    create_room(client, token, name='Big', capacity=10, equipment='Projector, HDMI', location='Floor 2')
    # capacity filter
    cap_resp = client.get('/rooms/available?capacity=8').get_json()
    cap_names = [x['name'] for x in cap_resp]
    assert 'Big' in cap_names and 'Small' not in cap_names
    # location filter
    loc_resp = client.get('/rooms/available?location=Floor%203').get_json()
    loc_names = [x['name'] for x in loc_resp]
    assert 'Small' in loc_names and 'Big' not in loc_names
    # equipment filter (needs HDMI & Projector)
    eq_resp = client.get('/rooms/available?equipment=HDMI,Projector').get_json()
    eq_names = [x['name'] for x in eq_resp]
    assert 'Big' in eq_names and 'Small' not in eq_names

def test_room_status_default_available(client):
    token = create_admin_and_get_token()
    room_id = create_room(client, token).get_json()['id']
    r = client.get(f'/rooms/{room_id}/status')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'available'


def test_create_room_missing_fields(client):
    token = create_admin_and_get_token()
    r = client.post('/rooms', json={'capacity': 5}, headers=_auth_header(token))  # missing name
    assert r.status_code == 400
    r2 = client.post('/rooms', json={'name': 'X'}, headers=_auth_header(token))  # missing capacity
    assert r2.status_code == 400

def test_create_room_invalid_capacity(client):
    token = create_admin_and_get_token()
    r = client.post('/rooms', json={'name': 'Bad', 'capacity': -1}, headers=_auth_header(token))
    assert r.status_code == 400
    r2 = client.post('/rooms', json={'name': 'Bad2', 'capacity': 'abc'}, headers=_auth_header(token))
    assert r2.status_code == 400

def test_update_room_no_fields(client):
    token = create_admin_and_get_token()
    room_id = create_room(client, token).get_json()['id']
    r = client.patch(f'/rooms/{room_id}', json={}, headers=_auth_header(token))
    assert r.status_code == 400

def test_update_room_invalid_capacity(client):
    token = create_admin_and_get_token()
    room_id = create_room(client, token).get_json()['id']
    r = client.patch(f'/rooms/{room_id}', json={'capacity': 0}, headers=_auth_header(token))
    assert r.status_code == 400
    r2 = client.patch(f'/rooms/{room_id}', json={'capacity': 'xyz'}, headers=_auth_header(token))
    assert r2.status_code == 400

def test_delete_room_not_found(client):
    token = create_admin_and_get_token()
    r = client.delete('/rooms/9999', headers=_auth_header(token))
    assert r.status_code == 404

def test_room_status_not_found(client):
    r = client.get('/rooms/9999/status')
    assert r.status_code == 404

def test_available_rooms_invalid_capacity(client):
    r = client.get('/rooms/available?capacity=abc')
    assert r.status_code == 400



def test_create_room_unauthorized_no_token(client):
    r = client.post('/rooms', json={'name': 'X', 'capacity': 5})
    assert r.status_code == 401

def test_create_room_forbidden_non_admin(client):
    # create normal user (self register) then get token
    uc = users_app.test_client()
    uc.post('/users/register', json={'username': 'u1', 'email': 'u1@example.com', 'password': 'Pass123!'} )
    login_r = uc.post('/auth/login', json={'username': 'u1', 'password': 'Pass123!'})
    token = login_r.get_json()['access_token']
    r = client.post('/rooms', json={'name': 'R', 'capacity': 5}, headers=_auth_header(token))
    assert r.status_code == 403

def test_update_room_unauthorized_no_token(client):
    r = client.patch('/rooms/1', json={'capacity': 10})
    assert r.status_code == 401

def test_delete_room_unauthorized_no_token(client):
    r = client.delete('/rooms/1')
    assert r.status_code == 401

def test_update_room_forbidden_non_admin(client):
    # need an admin to ensure role distinction
    admin_tok = create_admin_and_get_token()
    room_id = create_room(client, admin_tok).get_json()['id']
    # create normal user
    uc = users_app.test_client()
    uc.post('/users/register', json={'username': 'userx', 'email': 'userx@example.com', 'password': 'Pass123!'} )
    user_tok = uc.post('/auth/login', json={'username': 'userx', 'password': 'Pass123!'}).get_json()['access_token']
    r = client.patch(f'/rooms/{room_id}', json={'capacity': 9}, headers={'Authorization': f'Bearer {user_tok}'})
    assert r.status_code == 403

def test_delete_room_forbidden_non_admin(client):
    admin_tok = create_admin_and_get_token()
    room_id = create_room(client, admin_tok).get_json()['id']
    uc = users_app.test_client()
    uc.post('/users/register', json={'username': 'userz', 'email': 'userz@example.com', 'password': 'Pass123!'} )
    user_tok = uc.post('/auth/login', json={'username': 'userz', 'password': 'Pass123!'}).get_json()['access_token']
    r = client.delete(f'/rooms/{room_id}', headers={'Authorization': f'Bearer {user_tok}'})
    assert r.status_code == 403

def test_update_room_not_found_admin(client):
    admin_tok = create_admin_and_get_token()
    r = client.patch('/rooms/9999', json={'capacity': 10}, headers=_auth_header(admin_tok))
    assert r.status_code == 404

def test_invalid_token_for_admin_actions(client):
    r = client.post('/rooms', json={'name': 'X', 'capacity': 5}, headers={'Authorization': 'Bearer invalidtoken'})
    assert r.status_code == 401
