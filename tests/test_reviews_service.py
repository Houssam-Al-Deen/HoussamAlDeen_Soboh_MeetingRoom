import os
import sys
import time
import pytest

# Add project root to path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# DB env (match docker compose)
os.environ.setdefault('POSTGRES_USER', 'smr')
os.environ.setdefault('POSTGRES_PASSWORD', 'smr_pass')
os.environ.setdefault('POSTGRES_DB', 'smart_meeting_room')
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5433'

from shared.db import get_conn
from services.users_service.app import app as users_app
from services.rooms_service.app import app as rooms_app
from services.reviews_service.app import app as reviews_app

# Wait a moment for DB readiness
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
    return reviews_app.test_client()

def register_user(username='user1'):
    c = users_app.test_client()
    r = c.post('/users/register', json={
        'username': username,
        'email': f'{username}@example.com',
        'password': 'Pass123!',
        'full_name': 'Test User'
    })
    return r.get_json()['id']

def create_room(name='Room1'):
    c = rooms_app.test_client()
    r = c.post('/rooms', json={
        'name': name,
        'capacity': 5,
        'equipment': 'TV',
        'location': 'Floor 1'
    })
    return r.get_json()['id']

# ---------------- Happy path tests ----------------

def test_create_review_success(client):
    uid = register_user(); rid = create_room()
    r = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 5, 'comment': 'Great room'})
    assert r.status_code == 201
    body = r.get_json()
    assert body['user_id'] == uid and body['room_id'] == rid and body['rating'] == 5


def test_list_room_reviews(client):
    uid = register_user(); rid = create_room()
    client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 4, 'comment': 'Nice'})
    client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 5, 'comment': 'Excellent'})
    r = client.get(f'/rooms/{rid}/reviews')
    assert r.status_code == 200
    data = r.get_json()
    assert len(data) == 2
    ratings = sorted([d['rating'] for d in data])
    assert ratings == [4,5]


def test_update_review_rating_and_comment(client):
    uid = register_user(); rid = create_room()
    rev = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 3, 'comment': 'Ok'}).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={'rating': 4, 'comment': 'Better now'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['rating'] == 4 and 'Better' in body['comment']


def test_flag_review(client):
    uid = register_user(); rid = create_room()
    rev = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 5, 'comment': 'Great'}).get_json()
    r = client.post(f"/reviews/{rev['id']}/flag", json={'reason': 'Spam'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['is_flagged'] is True and body['flag_reason'] == 'Spam'


def test_delete_review(client):
    uid = register_user(); rid = create_room()
    rev = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 2, 'comment': 'Noisy'}).get_json()
    r = client.delete(f"/reviews/{rev['id']}")
    assert r.status_code == 200
    # listing should be empty
    lst = client.get(f'/rooms/{rid}/reviews').get_json()
    assert lst == []

# ---------------- Unhappy path tests ----------------

def test_create_review_missing_fields(client):
    r = client.post('/reviews', json={'room_id': 1, 'rating': 5})  # missing user_id
    assert r.status_code == 400
    r2 = client.post('/reviews', json={'user_id': 1, 'rating': 5})  # missing room_id
    assert r2.status_code == 400
    r3 = client.post('/reviews', json={'user_id': 1, 'room_id': 1})  # missing rating
    assert r3.status_code == 400


def test_create_review_invalid_rating(client):
    uid = register_user(); rid = create_room()
    r = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 0})
    assert r.status_code == 400
    r2 = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 6})
    assert r2.status_code == 400
    r3 = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 'bad'})
    assert r3.status_code == 400


def test_create_review_room_not_found(client):
    uid = register_user()
    r = client.post('/reviews', json={'user_id': uid, 'room_id': 9999, 'rating': 3})
    assert r.status_code == 404


def test_create_review_user_not_found(client):
    rid = create_room()
    r = client.post('/reviews', json={'user_id': 9999, 'room_id': rid, 'rating': 3})
    assert r.status_code == 404


def test_update_review_not_found(client):
    r = client.patch('/reviews/9999', json={'comment': 'x'})
    assert r.status_code == 404


def test_update_review_no_fields(client):
    uid = register_user(); rid = create_room(); rev = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 1}).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={})
    assert r.status_code == 400


def test_update_review_invalid_rating(client):
    uid = register_user(); rid = create_room(); rev = client.post('/reviews', json={'user_id': uid, 'room_id': rid, 'rating': 2}).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={'rating': 10})
    assert r.status_code == 400
    r2 = client.patch(f"/reviews/{rev['id']}", json={'rating': 0})
    assert r2.status_code == 400


def test_flag_review_not_found(client):
    r = client.post('/reviews/9999/flag', json={'reason': 'Spam'})
    assert r.status_code == 404


def test_delete_review_not_found(client):
    r = client.delete('/reviews/9999')
    assert r.status_code == 404
