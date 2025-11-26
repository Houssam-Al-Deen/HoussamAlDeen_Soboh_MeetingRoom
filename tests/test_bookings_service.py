import os
import sys
import time
import pytest
from datetime import datetime, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_DB'] = 'smart_meeting_room'
os.environ['POSTGRES_PORT'] = '5433'  # Docker exposed port

# Point services to localhost for HTTP testing
os.environ['USERS_SERVICE_URL'] = 'http://localhost:8001'
os.environ['ROOMS_SERVICE_URL'] = 'http://localhost:8002'
os.environ['BOOKINGS_SERVICE_URL'] = 'http://localhost:8003'
os.environ['REVIEWS_SERVICE_URL'] = 'http://localhost:8004'

from shared.db import get_conn
from services.users_service.app import app as users_app
from services.rooms_service.app import app as rooms_app
from services.bookings_service.app import app as bookings_app
API_PREFIX = f"/api/{os.getenv('API_VERSION','v1')}"

def _wait_db():
    for _ in range(30):
        try:
            c = get_conn(); c.close(); return
        except Exception:
            time.sleep(0.3)
_wait_db()

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
    return bookings_app.test_client()

def _auth(token):
    return {'Authorization': f'Bearer {token}'}

def create_admin_token():
    c = users_app.test_client()
    r = c.post(f'{API_PREFIX}/users/register', json={'username': 'admin', 'email': 'admin@example.com', 'password': 'AdminPass123', 'role': 'admin'})
    assert r.status_code == 201
    tok = c.post(f'{API_PREFIX}/auth/login', json={'username': 'admin', 'password': 'AdminPass123'}).get_json()['access_token']
    return tok

def create_user_and_token(username='u1'):
    c = users_app.test_client()
    assert c.post(f'{API_PREFIX}/users/register', json={'username': username, 'email': f'{username}@example.com', 'password': 'Pass123!'}).status_code == 201
    return c.post(f'{API_PREFIX}/auth/login', json={'username': username, 'password': 'Pass123!'}).get_json()['access_token']

def create_room(admin_tok, name='Room1', capacity=5):
    rc = rooms_app.test_client()
    r = rc.post(f'{API_PREFIX}/rooms', json={'name': name, 'capacity': capacity}, headers=_auth(admin_tok))
    return r.get_json()['id']

def iso(start_minutes=0, duration_minutes=60):
    base = datetime(2025, 11, 25, 10, 0, 0) + timedelta(minutes=start_minutes)
    end = base + timedelta(minutes=duration_minutes)
    return base.strftime('%Y-%m-%dT%H:%M:%S'), end.strftime('%Y-%m-%dT%H:%M:%S')



def test_admin_creates_booking_for_other_user(client):
    admin_tok = create_admin_token()
    user_tok = create_user_and_token('bob')
    # Get bob's user id directly
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='bob'"); bob_id = cur.fetchone()[0]; cur.close(); conn.close()
    room_id = create_room(admin_tok)
    start, end = iso()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': bob_id, 'room_id': room_id, 'start_time': start, 'end_time': end}, headers=_auth(admin_tok))
    assert r.status_code == 201 and r.get_json()['user_id'] == bob_id

def test_user_creates_own_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('alice')
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='alice'"); alice_id = cur.fetchone()[0]; cur.close(); conn.close()
    start, end = iso()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': alice_id, 'room_id': room_id, 'start_time': start, 'end_time': end}, headers=_auth(user_tok))
    assert r.status_code == 201

def test_list_bookings_admin_vs_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok1 = create_user_and_token('u1'); user_tok2 = create_user_and_token('u2')
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='u1'"); u1_id = cur.fetchone()[0]; cur.execute("SELECT id FROM users WHERE username='u2'"); u2_id = cur.fetchone()[0]; cur.close(); conn.close()
    s1,e1 = iso(0,60); s2,e2 = iso(120,60)
    assert client.post(f'{API_PREFIX}/bookings', json={'user_id': u1_id,'room_id': room_id,'start_time': s1,'end_time': e1}, headers=_auth(user_tok1)).status_code == 201
    assert client.post(f'{API_PREFIX}/bookings', json={'user_id': u2_id,'room_id': room_id,'start_time': s2,'end_time': e2}, headers=_auth(user_tok2)).status_code == 201
    # admin sees both
    admin_list = client.get(f'{API_PREFIX}/bookings', headers=_auth(admin_tok)).get_json()
    assert len(admin_list) == 2
    # u1 sees only own
    u1_list = client.get(f'{API_PREFIX}/bookings', headers=_auth(user_tok1)).get_json()
    assert len(u1_list) == 1 and u1_list[0]['user_id'] == u1_id

def test_user_updates_own_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('sam'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='sam'"); sam_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': sam_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    new_end = (datetime.fromisoformat(e) + timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M:%S')
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'end_time': new_end}, headers=_auth(user_tok))
    assert r.status_code == 200 and r.get_json()['end_time'].endswith('30:00')

def test_admin_updates_other_users_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('tom'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='tom'"); tom_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': tom_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    new_start,new_end = iso(180,60)
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'start_time': new_start,'end_time': new_end}, headers=_auth(admin_tok))
    assert r.status_code == 200 and r.get_json()['start_time'].startswith('2025-11-25T13')

def test_admin_force_updates_non_active_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('eve'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='eve'"); eve_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': eve_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    # cancel booking (active -> canceled)
    assert client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(user_tok)).status_code == 200
    new_start,new_end = iso(240,60)
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'start_time': new_start,'end_time': new_end,'force': True}, headers=_auth(admin_tok))
    assert r.status_code == 200 and r.get_json()['start_time'] == new_start

def test_admin_force_bypasses_conflict(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('zoe'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='zoe'"); zoe_id = cur.fetchone()[0]; cur.close(); conn.close()
    s1,e1 = iso(0,120); s2,e2 = iso(180,60)
    b1 = client.post(f'{API_PREFIX}/bookings', json={'user_id': zoe_id,'room_id': room_id,'start_time': s1,'end_time': e1}, headers=_auth(user_tok)).get_json()
    b2 = client.post(f'{API_PREFIX}/bookings', json={'user_id': zoe_id,'room_id': room_id,'start_time': s2,'end_time': e2}, headers=_auth(user_tok)).get_json()
    # move second into overlap with first forcibly
    overlap_start, overlap_end = iso(60,60)  # inside first booking timeframe
    r = client.patch(f"{API_PREFIX}/bookings/{b2['id']}", json={'start_time': overlap_start,'end_time': overlap_end,'force': True}, headers=_auth(admin_tok))
    assert r.status_code == 200

def test_user_cancels_own_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('lee'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='lee'"); lee_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': lee_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    r = client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(user_tok))
    assert r.status_code == 200 and r.get_json()['status'] == 'canceled'

def test_admin_cancels_other_users_active_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('mia'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='mia'"); mia_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': mia_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    r = client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(admin_tok))
    assert r.status_code == 200

def test_admin_force_cancel_non_active_booking(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('neo'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='neo'"); neo_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': neo_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    assert client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(user_tok)).status_code == 200
    r = client.post(f"{API_PREFIX}/bookings/{b['id']}/force-cancel", headers=_auth(admin_tok))
    assert r.status_code == 200 and r.get_json()['status'] == 'canceled'

def test_check_availability_flow(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('ava'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='ava'"); ava_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso(); r1 = client.get(f"{API_PREFIX}/bookings/check?room_id={room_id}&start={s}&end={e}")
    assert r1.status_code == 200 and r1.get_json()['available'] is True
    assert client.post(f'{API_PREFIX}/bookings', json={'user_id': ava_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).status_code == 201
    r2 = client.get(f"{API_PREFIX}/bookings/check?room_id={room_id}&start={s}&end={e}")
    assert r2.status_code == 200 and r2.get_json()['available'] is False



def test_create_booking_forbidden_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_and_token('joe'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='joe'"); joe_id = cur.fetchone()[0]; cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close()
    s,e = iso()
    # joe attempts booking for admin id
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok))
    assert r.status_code == 403

def test_create_booking_missing_fields(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id}, headers=_auth(admin_tok))
    assert r.status_code == 400

def test_create_booking_invalid_times(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': '2025-11-25T12:00:00','end_time': '2025-11-25T11:00:00'}, headers=_auth(admin_tok))
    assert r.status_code == 400

def test_create_booking_bad_iso(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': 'bad','end_time': 'also'}, headers=_auth(admin_tok))
    assert r.status_code == 400

def test_create_booking_user_not_found(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); s,e = iso()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': 9999,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(admin_tok))
    assert r.status_code == 404

def test_create_booking_room_not_found(client):
    admin_tok = create_admin_token(); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': 9999,'start_time': s,'end_time': e}, headers=_auth(admin_tok))
    assert r.status_code == 404

def test_create_booking_conflict(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso()
    assert client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(admin_tok)).status_code == 201
    r2 = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(admin_tok))
    assert r2.status_code == 409

def test_create_booking_unauthorized_no_token(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); s,e = iso(); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close()
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e})
    assert r.status_code == 401

def test_create_booking_invalid_token(client):
    r = client.post(f'{API_PREFIX}/bookings', json={'user_id': 1,'room_id': 1,'start_time': 'x','end_time': 'y'}, headers={'Authorization': 'Bearer badtoken'})
    assert r.status_code == 401

def test_list_bookings_unauthorized(client):
    r = client.get(f'{API_PREFIX}/bookings')
    assert r.status_code == 401

def test_list_bookings_invalid_token(client):
    r = client.get(f'{API_PREFIX}/bookings', headers={'Authorization': 'Bearer badtoken'})
    assert r.status_code == 401

def test_update_booking_not_found(client):
    admin_tok = create_admin_token()
    r = client.patch(f'{API_PREFIX}/bookings/9999', json={'end_time': '2025-11-25T12:00:00'}, headers=_auth(admin_tok))
    assert r.status_code == 404

def test_update_booking_no_fields(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(admin_tok)).get_json()
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={}, headers=_auth(admin_tok))
    assert r.status_code == 400

def test_update_booking_invalid_times(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(admin_tok)).get_json()
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'start_time': 'bad'}, headers=_auth(admin_tok))
    assert r.status_code == 400

def test_update_booking_conflict_without_force(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close()
    s1,e1 = iso(0,120); s2,e2 = iso(180,60); b1 = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s1,'end_time': e1}, headers=_auth(admin_tok)).get_json(); b2 = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s2,'end_time': e2}, headers=_auth(admin_tok)).get_json()
    overlap_s, overlap_e = iso(60,60)
    r = client.patch(f"{API_PREFIX}/bookings/{b2['id']}", json={'start_time': overlap_s,'end_time': overlap_e}, headers=_auth(admin_tok))
    assert r.status_code == 409

def test_update_booking_room_not_found(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='admin'"); admin_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': admin_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(admin_tok)).get_json()
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'room_id': 99999}, headers=_auth(admin_tok))
    assert r.status_code == 404

def test_update_booking_forbidden_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_and_token('bob'); other_tok = create_user_and_token('carol')
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='bob'"); bob_id = cur.fetchone()[0]; cur.execute("SELECT id FROM users WHERE username='carol'"); carol_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': bob_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json()
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'end_time': e}, headers=_auth(other_tok))
    assert r.status_code == 403

def test_update_booking_non_active_without_force(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_and_token('dan'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='dan'"); dan_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': dan_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(user_tok)).get_json(); assert client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(user_tok)).status_code == 200
    r = client.patch(f"{API_PREFIX}/bookings/{b['id']}", json={'end_time': e}, headers=_auth(user_tok))
    assert r.status_code == 400

def test_update_booking_unauthorized_no_token(client):
    r = client.patch(f'{API_PREFIX}/bookings/1', json={'end_time': '2025-11-25T11:00:00'})
    assert r.status_code == 401

def test_update_booking_invalid_token(client):
    r = client.patch(f'{API_PREFIX}/bookings/1', json={'end_time': '2025-11-25T11:00:00'}, headers={'Authorization': 'Bearer bad'})
    assert r.status_code == 401

def test_cancel_booking_not_found(client):
    admin_tok = create_admin_token(); r = client.delete(f'{API_PREFIX}/bookings/9999', headers=_auth(admin_tok))
    assert r.status_code == 404

def test_cancel_booking_forbidden_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); tok1 = create_user_and_token('aa'); tok2 = create_user_and_token('bb'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='aa'"); aa_id = cur.fetchone()[0]; cur.execute("SELECT id FROM users WHERE username='bb'"); bb_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': aa_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(tok1)).get_json()
    r = client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(tok2))
    assert r.status_code == 403

def test_cancel_booking_non_active(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); tok = create_user_and_token('cc'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='cc'"); cc_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': cc_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(tok)).get_json(); assert client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(tok)).status_code == 200
    r = client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(tok))
    assert r.status_code == 400
    r_admin = client.delete(f"{API_PREFIX}/bookings/{b['id']}", headers=_auth(admin_tok))
    assert r_admin.status_code == 400

def test_cancel_booking_unauthorized_no_token(client):
    r = client.delete(f'{API_PREFIX}/bookings/1')
    assert r.status_code == 401

def test_cancel_booking_invalid_token(client):
    r = client.delete(f'{API_PREFIX}/bookings/1', headers={'Authorization': 'Bearer bad'})
    assert r.status_code == 401

def test_force_cancel_not_found(client):
    admin_tok = create_admin_token(); r = client.post(f'{API_PREFIX}/bookings/99999/force-cancel', headers=_auth(admin_tok))
    assert r.status_code == 404

def test_force_cancel_forbidden_non_admin(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); tok = create_user_and_token('dd'); conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='dd'"); dd_id = cur.fetchone()[0]; cur.close(); conn.close(); s,e = iso(); b = client.post(f'{API_PREFIX}/bookings', json={'user_id': dd_id,'room_id': room_id,'start_time': s,'end_time': e}, headers=_auth(tok)).get_json()
    r = client.post(f"{API_PREFIX}/bookings/{b['id']}/force-cancel", headers=_auth(tok))
    assert r.status_code == 403

def test_force_cancel_invalid_token(client):
    r = client.post(f'{API_PREFIX}/bookings/1/force-cancel', headers={'Authorization': 'Bearer bad'})
    assert r.status_code == 401

def test_force_cancel_unauthorized_no_token(client):
    r = client.post(f'{API_PREFIX}/bookings/1/force-cancel')
    assert r.status_code == 401

def test_check_availability_missing_params(client):
    r = client.get(f'{API_PREFIX}/bookings/check?room_id=1&start=2025-11-25T10:00:00')
    assert r.status_code == 400

def test_check_availability_bad_room_id(client):
    s,e = iso(); r = client.get(f"{API_PREFIX}/bookings/check?room_id=abc&start={s}&end={e}")
    assert r.status_code == 400

def test_check_availability_invalid_times(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    r = client.get(f"{API_PREFIX}/bookings/check?room_id={room_id}&start=2025-11-25T12:00:00&end=2025-11-25T11:00:00")
    assert r.status_code == 400

def test_check_availability_room_not_found(client):
    s,e = iso(); r = client.get(f"{API_PREFIX}/bookings/check?room_id=99999&start={s}&end={e}")
    assert r.status_code == 404

def test_room_active_status_endpoint(client):
    """Verify the new /bookings/room/<id>/active-status endpoint reflects live booking state."""
    admin_tok = create_admin_token()
    room_id = create_room(admin_tok)
    user_tok = create_user_and_token('live')
    # Acquire user id
    conn = get_conn(); cur = conn.cursor(); cur.execute("SELECT id FROM users WHERE username='live'"); live_id = cur.fetchone()[0]; cur.close(); conn.close()
    # Create a booking overlapping NOW() so active-status returns 'booked'
    from datetime import datetime, timedelta
    now = datetime.now()
    start = (now - timedelta(minutes=10)).replace(microsecond=0).isoformat()
    end = (now + timedelta(minutes=50)).replace(microsecond=0).isoformat()
    create_resp = client.post(f'{API_PREFIX}/bookings', json={'user_id': live_id, 'room_id': room_id, 'start_time': start, 'end_time': end}, headers=_auth(user_tok))
    assert create_resp.status_code == 201
    status_resp = client.get(f'{API_PREFIX}/bookings/room/{room_id}/active-status')
    assert status_resp.status_code == 200 and status_resp.get_json()['status'] == 'booked'
