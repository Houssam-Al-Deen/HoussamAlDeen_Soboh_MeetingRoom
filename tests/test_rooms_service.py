import os
import sys
import time
import pytest

# Put project root on path (so imports work when running tests directly)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Basic DB settings (match docker-compose exposed port)
os.environ.setdefault('POSTGRES_USER', 'smr')
os.environ.setdefault('POSTGRES_PASSWORD', 'smr_pass')
os.environ.setdefault('POSTGRES_DB', 'smart_meeting_room')
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5433'

from shared.db import get_conn
from services.rooms_service.app import app

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
    # Only need rooms + bookings cleared for these simple tests
    cur.execute("TRUNCATE bookings RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE rooms RESTART IDENTITY CASCADE")
    conn.commit(); cur.close(); conn.close()
    yield

@pytest.fixture
def client():
    return app.test_client()

def create_room(client, name="RoomA", capacity=5, equipment="TV, HDMI", location="Floor 1"):
    return client.post('/rooms', json={
        'name': name,
        'capacity': capacity,
        'equipment': equipment,
        'location': location
    })

def test_create_room(client):
    r = create_room(client)
    assert r.status_code == 201
    body = r.get_json()
    assert body['name'] == 'RoomA'
    assert body['capacity'] == 5

def test_duplicate_room_name(client):
    assert create_room(client).status_code == 201
    r2 = create_room(client)
    assert r2.status_code == 409

def test_list_rooms(client):
    create_room(client, name='R1')
    create_room(client, name='R2')
    r = client.get('/rooms')
    assert r.status_code == 200
    names = [x['name'] for x in r.get_json()]
    assert 'R1' in names and 'R2' in names

def test_update_room(client):
    room_id = create_room(client).get_json()['id']
    r = client.patch(f'/rooms/{room_id}', json={'capacity': 10, 'equipment': 'Projector'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['capacity'] == 10
    assert 'Projector' in body['equipment']

def test_delete_room(client):
    room_id = create_room(client).get_json()['id']
    r = client.delete(f'/rooms/{room_id}')
    assert r.status_code == 200
    # Should no longer appear in list
    lst = client.get('/rooms').get_json()
    ids = [x['id'] for x in lst]
    assert room_id not in ids

def test_available_rooms_filters(client):
    create_room(client, name='Small', capacity=4, equipment='TV, Whiteboard', location='Floor 3')
    create_room(client, name='Big', capacity=10, equipment='Projector, HDMI', location='Floor 2')
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
    room_id = create_room(client).get_json()['id']
    r = client.get(f'/rooms/{room_id}/status')
    assert r.status_code == 200
    assert r.get_json()['status'] == 'available'

# ---------------- Unhappy path tests (error cases) ----------------

def test_create_room_missing_fields(client):
    r = client.post('/rooms', json={'capacity': 5})  # missing name
    assert r.status_code == 400
    r2 = client.post('/rooms', json={'name': 'X'})  # missing capacity
    assert r2.status_code == 400

def test_create_room_invalid_capacity(client):
    r = client.post('/rooms', json={'name': 'Bad', 'capacity': -1})
    assert r.status_code == 400
    r2 = client.post('/rooms', json={'name': 'Bad2', 'capacity': 'abc'})
    assert r2.status_code == 400

def test_update_room_no_fields(client):
    room_id = create_room(client).get_json()['id']
    r = client.patch(f'/rooms/{room_id}', json={})
    assert r.status_code == 400

def test_update_room_invalid_capacity(client):
    room_id = create_room(client).get_json()['id']
    r = client.patch(f'/rooms/{room_id}', json={'capacity': 0})
    assert r.status_code == 400
    r2 = client.patch(f'/rooms/{room_id}', json={'capacity': 'xyz'})
    assert r2.status_code == 400

def test_delete_room_not_found(client):
    r = client.delete('/rooms/9999')
    assert r.status_code == 404

def test_room_status_not_found(client):
    r = client.get('/rooms/9999/status')
    assert r.status_code == 404

def test_available_rooms_invalid_capacity(client):
    r = client.get('/rooms/available?capacity=abc')
    assert r.status_code == 400
