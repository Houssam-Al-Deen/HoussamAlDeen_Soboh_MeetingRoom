import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from memory_profiler import profile

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from services.users_service.app import app as users_app  # noqa


@profile
def main():
    client = users_app.test_client()
    client.post('/users/register', json={'username': 'admin', 'email': 'admin@example.com', 'password': 'x', 'role': 'admin', 'full_name': 'Admin'})
    client.post('/users/register', json={'username': 'bob', 'email': 'bob@example.com', 'password': 'x', 'full_name': 'Bob'})
    admin_tok = client.post('/auth/login', json={'username': 'admin', 'password': 'x'}).get_json().get('access_token')
    bob_tok = client.post('/auth/login', json={'username': 'bob', 'password': 'x'}).get_json().get('access_token')
    h_admin = {'Authorization': f'Bearer {admin_tok}'}
    h_bob = {'Authorization': f'Bearer {bob_tok}'}
    client.get('/users', headers=h_admin)
    client.get('/users/me', headers=h_bob)
    client.patch('/users/me', json={'full_name': 'Bob Z'}, headers=h_bob)
    client.get('/users/bob', headers=h_bob)
    client.patch('/users/bob', json={'role': 'moderator'}, headers=h_admin)
    client.get('/users/bob/bookings', headers=h_bob)
    client.delete('/users/me', headers=h_bob)
    client.delete('/users/bob', headers=h_admin)


if __name__ == '__main__':
    main()
