import os
import time
import jwt
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from line_profiler import LineProfiler

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from services.users_service.app import app as users_app, register_user, list_users, login, get_me, update_me, delete_me, admin_update_user, admin_delete_user, get_user_by_username, user_booking_history  # noqa

JWT_SECRET = os.getenv('JWT_SECRET', 'devsecret')


def unwrap(fn):
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def bootstrap_initial():
    client = users_app.test_client()
    client.post('/users/register', json={'username': 'admin', 'email': 'admin@example.com', 'password': 'x', 'role': 'admin', 'full_name': 'Admin'})
    client.post('/users/register', json={'username': 'alice', 'email': 'alice@example.com', 'password': 'x', 'full_name': 'Alice'})


def main_flow():
    bootstrap_initial()
    client = users_app.test_client()
    admin_token = client.post('/auth/login', json={'username': 'admin', 'password': 'x'}).get_json().get('access_token')
    user_token = client.post('/auth/login', json={'username': 'alice', 'password': 'x'}).get_json().get('access_token')
    h_admin = {'Authorization': f'Bearer {admin_token}'}
    h_user = {'Authorization': f'Bearer {user_token}'}
    client.get('/users', headers=h_admin)
    client.get('/users/me', headers=h_user)
    client.patch('/users/me', json={'full_name': 'Alice Q'}, headers=h_user)
    client.get('/users/alice', headers=h_user)
    client.patch('/users/alice', json={'role': 'moderator'}, headers=h_admin)
    client.get('/users/alice/bookings', headers=h_user)
    client.delete('/users/alice', headers=h_admin)
    client.post('/users/register', json={'username': 'temp', 'email': 'temp@example.com', 'password': 'x', 'full_name': 'Temp'})
    temp_token = client.post('/auth/login', json={'username': 'temp', 'password': 'x'}).get_json().get('access_token')
    h_temp = {'Authorization': f'Bearer {temp_token}'}
    client.get('/users/me', headers=h_temp)
    client.delete('/users/me', headers=h_temp)


def profile_main():
    profiler = LineProfiler()
    profiler.add_function(unwrap(register_user))
    profiler.add_function(unwrap(list_users))
    profiler.add_function(unwrap(login))
    profiler.add_function(unwrap(get_me))
    profiler.add_function(unwrap(update_me))
    profiler.add_function(unwrap(delete_me))
    profiler.add_function(unwrap(admin_update_user))
    profiler.add_function(unwrap(admin_delete_user))
    profiler.add_function(unwrap(get_user_by_username))
    profiler.add_function(unwrap(user_booking_history))
    profiler.runcall(main_flow)
    profiler.print_stats()


if __name__ == '__main__':
    profile_main()
