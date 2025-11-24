import os
import sys
import time
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from shared.db import get_conn
from services.users_service.app import app as users_app
from services.rooms_service.app import app as rooms_app
from services.reviews_service.app import app as reviews_app

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
    return reviews_app.test_client()

def _auth(tok):
    return {'Authorization': f'Bearer {tok}'}

def create_admin_token():
    c = users_app.test_client()
    assert c.post('/users/register', json={'username':'admin','email':'admin@example.com','password':'AdminPass123','role':'admin'}).status_code == 201
    return c.post('/auth/login', json={'username':'admin','password':'AdminPass123'}).get_json()['access_token']

def create_user_token(username='u1'):
    c = users_app.test_client()
    assert c.post('/users/register', json={'username':username,'email':f'{username}@example.com','password':'Pass123!'}).status_code == 201
    return c.post('/auth/login', json={'username':username,'password':'Pass123!'}).get_json()['access_token']

def create_moderator_token():
    c = users_app.test_client()
    # Try login existing admin first
    login_admin = c.post('/auth/login', json={'username':'admin','password':'AdminPass123'})
    if login_admin.status_code == 200:
        admin_tok = login_admin.get_json()['access_token']
    else:
        # Bootstrap admin then login
        r_admin = c.post('/users/register', json={'username':'admin','email':'admin@example.com','password':'AdminPass123','role':'admin'})
        assert r_admin.status_code == 201
        admin_tok = c.post('/auth/login', json={'username':'admin','password':'AdminPass123'}).get_json()['access_token']
    # Register moderator if not exists
    r_mod = c.post('/users/register', json={'username':'mod','email':'mod@example.com','password':'ModPass123','role':'moderator'}, headers=_auth(admin_tok))
    if r_mod.status_code not in (201, 409):
        # Unexpected failure
        assert False, f"unexpected moderator registration status {r_mod.status_code}"
    # Login moderator
    mod_login = c.post('/auth/login', json={'username':'mod','password':'ModPass123'})
    assert mod_login.status_code == 200
    return mod_login.get_json()['access_token']

def create_room(admin_tok, name='Room1'):
    rc = rooms_app.test_client()
    return rc.post('/rooms', json={'name':name,'capacity':5}, headers=_auth(admin_tok)).get_json()['id']

def _get_user_id(username):
    conn = get_conn(); cur = conn.cursor(); cur.execute('SELECT id FROM users WHERE username=%s',(username,)); uid = cur.fetchone()[0]; cur.close(); conn.close(); return uid



def test_user_creates_own_review(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    user_tok = create_user_token('alice'); alice_id = _get_user_id('alice')
    r = client.post('/reviews', json={'user_id':alice_id,'room_id':room_id,'rating':5,'comment':'Great'}, headers=_auth(user_tok))
    assert r.status_code == 201 and r.get_json()['user_id'] == alice_id

def test_admin_creates_for_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('bob'); bob_id = _get_user_id('bob')
    r = client.post('/reviews', json={'user_id':bob_id,'room_id':room_id,'rating':4}, headers=_auth(admin_tok))
    assert r.status_code == 201 and r.get_json()['user_id'] == bob_id

def test_list_reviews_public(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('carol'); carol_id = _get_user_id('carol')
    client.post('/reviews', json={'user_id':carol_id,'room_id':room_id,'rating':3}, headers=_auth(user_tok))
    client.post('/reviews', json={'user_id':carol_id,'room_id':room_id,'rating':5}, headers=_auth(user_tok))
    r = client.get(f'/rooms/{room_id}/reviews')
    assert r.status_code == 200 and len(r.get_json()) == 2

def test_author_updates_own_review(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('dave'); dave_id = _get_user_id('dave')
    rev = client.post('/reviews', json={'user_id':dave_id,'room_id':room_id,'rating':2,'comment':'meh'}, headers=_auth(user_tok)).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={'rating':3,'comment':'better'}, headers=_auth(user_tok))
    assert r.status_code == 200 and r.get_json()['rating'] == 3

def test_moderator_updates_other_review(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('em'); em_id = _get_user_id('em'); mod_tok = create_moderator_token()
    rev = client.post('/reviews', json={'user_id':em_id,'room_id':room_id,'rating':1,'comment':'bad'}, headers=_auth(user_tok)).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={'comment':'edited by mod'}, headers=_auth(mod_tok))
    assert r.status_code == 200 and 'edited' in r.get_json()['comment']

def test_admin_deletes_other_review(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('frank'); frank_id = _get_user_id('frank')
    rev = client.post('/reviews', json={'user_id':frank_id,'room_id':room_id,'rating':4}, headers=_auth(user_tok)).get_json()
    r = client.delete(f"/reviews/{rev['id']}", headers=_auth(admin_tok))
    assert r.status_code == 200

def test_moderator_flags_review(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('gina'); gina_id = _get_user_id('gina'); mod_tok = create_moderator_token()
    rev = client.post('/reviews', json={'user_id':gina_id,'room_id':room_id,'rating':5,'comment':'spam'}, headers=_auth(user_tok)).get_json()
    r = client.post(f"/reviews/{rev['id']}/flag", json={'reason':'Spam'}, headers=_auth(mod_tok))
    assert r.status_code == 200 and r.get_json()['is_flagged'] is True



def test_create_review_forbidden_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('henry'); henry_id = _get_user_id('henry'); admin_id = _get_user_id('admin')
    r = client.post('/reviews', json={'user_id':admin_id,'room_id':room_id,'rating':5}, headers=_auth(user_tok))
    assert r.status_code == 403

def test_create_review_missing_fields(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('ivy'); ivy_id = _get_user_id('ivy')
    r1 = client.post('/reviews', json={'room_id':room_id,'rating':5}, headers=_auth(user_tok))
    assert r1.status_code == 400
    r2 = client.post('/reviews', json={'user_id':ivy_id,'rating':5}, headers=_auth(user_tok))
    assert r2.status_code == 400
    r3 = client.post('/reviews', json={'user_id':ivy_id,'room_id':room_id}, headers=_auth(user_tok))
    assert r3.status_code == 400

def test_create_review_invalid_rating(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('jack'); jack_id = _get_user_id('jack')
    r0 = client.post('/reviews', json={'user_id':jack_id,'room_id':room_id,'rating':0}, headers=_auth(user_tok))
    assert r0.status_code == 400
    r6 = client.post('/reviews', json={'user_id':jack_id,'room_id':room_id,'rating':6}, headers=_auth(user_tok))
    assert r6.status_code == 400
    r_bad = client.post('/reviews', json={'user_id':jack_id,'room_id':room_id,'rating':'bad'}, headers=_auth(user_tok))
    assert r_bad.status_code == 400

def test_create_review_room_not_found(client):
    admin_tok = create_admin_token(); user_tok = create_user_token('kate'); kate_id = _get_user_id('kate')
    r = client.post('/reviews', json={'user_id':kate_id,'room_id':99999,'rating':3}, headers=_auth(user_tok))
    assert r.status_code == 404

def test_create_review_user_not_found(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok)
    r = client.post('/reviews', json={'user_id':99999,'room_id':room_id,'rating':3}, headers=_auth(admin_tok))
    assert r.status_code == 404

def test_update_review_not_found(client):
    admin_tok = create_admin_token(); r = client.patch('/reviews/99999', json={'comment':'x'}, headers=_auth(admin_tok))
    assert r.status_code == 404

def test_update_review_no_fields(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('liz'); liz_id = _get_user_id('liz')
    rev = client.post('/reviews', json={'user_id':liz_id,'room_id':room_id,'rating':1}, headers=_auth(user_tok)).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={}, headers=_auth(user_tok))
    assert r.status_code == 400

def test_update_review_invalid_rating(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('max'); max_id = _get_user_id('max')
    rev = client.post('/reviews', json={'user_id':max_id,'room_id':room_id,'rating':2}, headers=_auth(user_tok)).get_json()
    r10 = client.patch(f"/reviews/{rev['id']}", json={'rating':10}, headers=_auth(user_tok))
    assert r10.status_code == 400
    r0 = client.patch(f"/reviews/{rev['id']}", json={'rating':0}, headers=_auth(user_tok))
    assert r0.status_code == 400

def test_update_review_forbidden_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); tok_a = create_user_token('amy'); tok_b = create_user_token('nick'); amy_id = _get_user_id('amy'); rev = client.post('/reviews', json={'user_id':amy_id,'room_id':room_id,'rating':3}, headers=_auth(tok_a)).get_json()
    r = client.patch(f"/reviews/{rev['id']}", json={'comment':'x'}, headers=_auth(tok_b))
    assert r.status_code == 403

def test_delete_review_not_found(client):
    admin_tok = create_admin_token(); r = client.delete('/reviews/99999', headers=_auth(admin_tok))
    assert r.status_code == 404

def test_delete_review_forbidden_other_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); tok_a = create_user_token('oliver'); tok_b = create_user_token('pete'); oliver_id = _get_user_id('oliver'); rev = client.post('/reviews', json={'user_id':oliver_id,'room_id':room_id,'rating':4}, headers=_auth(tok_a)).get_json()
    r = client.delete(f"/reviews/{rev['id']}", headers=_auth(tok_b))
    assert r.status_code == 403

def test_flag_review_not_found(client):
    mod_tok = create_moderator_token(); r = client.post('/reviews/99999/flag', json={'reason':'Spam'}, headers=_auth(mod_tok))
    assert r.status_code == 404

def test_flag_review_forbidden_user(client):
    admin_tok = create_admin_token(); room_id = create_room(admin_tok); user_tok = create_user_token('quinn'); quinn_id = _get_user_id('quinn')
    rev = client.post('/reviews', json={'user_id':quinn_id,'room_id':room_id,'rating':5}, headers=_auth(user_tok)).get_json()
    r = client.post(f"/reviews/{rev['id']}/flag", json={'reason':'Spam'}, headers=_auth(user_tok))
    assert r.status_code == 403

def test_protected_endpoints_unauthorized(client):
    r_create = client.post('/reviews', json={'user_id':1,'room_id':1,'rating':5})
    assert r_create.status_code == 401
    r_upd = client.patch('/reviews/1', json={'comment':'x'})
    assert r_upd.status_code == 401
    r_del = client.delete('/reviews/1')
    assert r_del.status_code == 401
    r_flag = client.post('/reviews/1/flag', json={'reason':'Spam'})
    assert r_flag.status_code == 401

def test_protected_endpoints_invalid_token(client):
    bad = {'Authorization':'Bearer invalid'}
    r_create = client.post('/reviews', json={'user_id':1,'room_id':1,'rating':5}, headers=bad)
    assert r_create.status_code == 401
    r_upd = client.patch('/reviews/1', json={'comment':'x'}, headers=bad)
    assert r_upd.status_code == 401
    r_del = client.delete('/reviews/1', headers=bad)
    assert r_del.status_code == 401
    r_flag = client.post('/reviews/1/flag', json={'reason':'Spam'}, headers=bad)
    assert r_flag.status_code == 401
