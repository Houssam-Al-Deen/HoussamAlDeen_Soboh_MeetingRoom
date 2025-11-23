import os
import sys
import time
import pytest
from datetime import datetime, timedelta

# Add project root so imports work
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# DB env (matches docker compose)
os.environ.setdefault('POSTGRES_USER', 'smr')
os.environ.setdefault('POSTGRES_PASSWORD', 'smr_pass')
os.environ.setdefault('POSTGRES_DB', 'smart_meeting_room')
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5433'

from shared.db import get_conn

# Import apps so endpoints are available
from services.users_service.app import app as users_app
from services.rooms_service.app import app as rooms_app
from services.bookings_service.app import app as bookings_app

# Wait for DB
for _ in range(30):
    try:
        c = get_conn(); c.close(); break
    except Exception:
        time.sleep(0.3)

@pytest.fixture(autouse=True)
def clean_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("TRUNCATE reviews RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE bookings RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE rooms RESTART IDENTITY CASCADE")
    cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
    conn.commit(); cur.close(); conn.close()
    yield

@pytest.fixture
def client():
    # Use bookings_app test client (others share same DB)
    return bookings_app.test_client()

def register_user(username="user1"):
    c = users_app.test_client()
    r = c.post('/users/register', json={
        'username': username,
        'email': f'{username}@example.com',
        'password': 'Pass123!',
        'full_name': 'Test User'
    })
    return r.get_json()['id']

def create_room(name="Room1"):
    c = rooms_app.test_client()
    r = c.post('/rooms', json={
        'name': name,
        'capacity': 5,
        'equipment': 'TV',
        'location': 'Floor 1'
    })
    return r.get_json()['id']

def iso(start_minutes=0, duration_minutes=60):
    base = datetime(2025, 11, 25, 10, 0, 0) + timedelta(minutes=start_minutes)
    end = base + timedelta(minutes=duration_minutes)
    return base.strftime('%Y-%m-%dT%H:%M:%S'), end.strftime('%Y-%m-%dT%H:%M:%S')

# ---------------- Happy path tests ----------------

def test_create_booking_success(client):
    uid = register_user(); rid = create_room()
    start, end = iso()
    r = client.post('/bookings', json={
        'user_id': uid,
        'room_id': rid,
        'start_time': start,
        'end_time': end
    })
    assert r.status_code == 201
    body = r.get_json()
    assert body['user_id'] == uid and body['room_id'] == rid


def test_list_bookings(client):
    uid = register_user(); rid = create_room()
    start, end = iso()
    client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end})
    r = client.get('/bookings')
    assert r.status_code == 200
    data = r.get_json()
    assert len(data) == 1
    assert data[0]['room_id'] == rid


def test_update_booking_time(client):
    uid = register_user(); rid = create_room()
    start, end = iso()
    b = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end}).get_json()
    new_end = datetime.fromisoformat(end) + timedelta(minutes=30)
    r = client.patch(f"/bookings/{b['id']}", json={'end_time': new_end.strftime('%Y-%m-%dT%H:%M:%S')})
    assert r.status_code == 200
    assert r.get_json()['end_time'].endswith('30:00')


def test_update_booking_change_room(client):
    uid = register_user(); rid1 = create_room('R1'); rid2 = create_room('R2')
    start, end = iso()
    b = client.post('/bookings', json={'user_id': uid, 'room_id': rid1, 'start_time': start, 'end_time': end}).get_json()
    r = client.patch(f"/bookings/{b['id']}", json={'room_id': rid2})
    assert r.status_code == 200
    assert r.get_json()['room_id'] == rid2


def test_cancel_booking(client):
    uid = register_user(); rid = create_room()
    start, end = iso()
    b = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end}).get_json()
    r = client.delete(f"/bookings/{b['id']}")
    assert r.status_code == 200
    assert r.get_json()['status'] == 'canceled'


def test_check_availability_before_and_after_booking(client):
    uid = register_user(); rid = create_room()
    start, end = iso()
    # Before booking
    r1 = client.get(f"/bookings/check?room_id={rid}&start={start}&end={end}")
    assert r1.status_code == 200 and r1.get_json()['available'] is True
    # Create booking
    client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end})
    # After booking
    r2 = client.get(f"/bookings/check?room_id={rid}&start={start}&end={end}")
    assert r2.status_code == 200 and r2.get_json()['available'] is False

# ---------------- Unhappy path tests ----------------

def test_create_booking_missing_fields(client):
    r = client.post('/bookings', json={'user_id': 1})
    assert r.status_code == 400


def test_create_booking_invalid_times(client):
    uid = register_user(); rid = create_room()
    r = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': '2025-11-25T11:00:00', 'end_time': '2025-11-25T10:00:00'})
    assert r.status_code == 400


def test_create_booking_bad_iso(client):
    uid = register_user(); rid = create_room()
    r = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': 'bad', 'end_time': 'also-bad'})
    assert r.status_code == 400


def test_create_booking_user_not_found(client):
    rid = create_room()
    start, end = iso()
    r = client.post('/bookings', json={'user_id': 999, 'room_id': rid, 'start_time': start, 'end_time': end})
    assert r.status_code == 404


def test_create_booking_room_not_found(client):
    uid = register_user()
    start, end = iso()
    r = client.post('/bookings', json={'user_id': uid, 'room_id': 999, 'start_time': start, 'end_time': end})
    assert r.status_code == 404


def test_create_booking_conflict(client):
    uid = register_user(); rid = create_room(); start, end = iso();
    assert client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end}).status_code == 201
    # Overlapping (start inside existing)
    r2 = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end})
    assert r2.status_code == 409


def test_update_booking_not_found(client):
    r = client.patch('/bookings/999', json={'end_time': '2025-11-25T12:00:00'})
    assert r.status_code == 404


def test_update_booking_no_fields(client):
    uid = register_user(); rid = create_room(); start, end = iso();
    b = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end}).get_json()
    r = client.patch(f"/bookings/{b['id']}", json={})
    assert r.status_code == 400


def test_update_booking_invalid_times(client):
    uid = register_user(); rid = create_room(); start, end = iso();
    b = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end}).get_json()
    r = client.patch(f"/bookings/{b['id']}", json={'start_time': 'bad'})
    assert r.status_code == 400


def test_update_booking_conflict(client):
    uid = register_user(); rid = create_room();
    s1, e1 = iso(0, 60)
    s2, e2 = iso(30, 60)  # overlaps with first
    b1 = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': s1, 'end_time': e1}).get_json()
    b2 = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': e1, 'end_time': iso(60,60)[1]}).get_json()
    # Try to move second booking into conflict window
    r = client.patch(f"/bookings/{b2['id']}", json={'start_time': s2, 'end_time': e2})
    assert r.status_code == 409


def test_update_booking_room_not_found(client):
    uid = register_user(); rid = create_room(); start, end = iso();
    b = client.post('/bookings', json={'user_id': uid, 'room_id': rid, 'start_time': start, 'end_time': end}).get_json()
    r = client.patch(f"/bookings/{b['id']}", json={'room_id': 9999})
    assert r.status_code == 404


def test_cancel_booking_not_found(client):
    r = client.delete('/bookings/9999')
    assert r.status_code == 404


def test_check_availability_missing_params(client):
    r = client.get('/bookings/check?room_id=1&start=2025-11-25T10:00:00')  # missing end
    assert r.status_code == 400


def test_check_availability_bad_room_id(client):
    start, end = iso()
    r = client.get(f"/bookings/check?room_id=abc&start={start}&end={end}")
    assert r.status_code == 400


def test_check_availability_invalid_times(client):
    rid = create_room();
    r = client.get(f"/bookings/check?room_id={rid}&start=2025-11-25T11:00:00&end=2025-11-25T10:00:00")
    assert r.status_code == 400
