import os
import sys
import cProfile
import pstats

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ['POSTGRES_USER'] = 'smr'
os.environ['POSTGRES_PASSWORD'] = 'smr_pass'
os.environ['POSTGRES_DB'] = 'smart_meeting_room_test'
os.environ['POSTGRES_HOST'] = '127.0.0.1'
os.environ['POSTGRES_PORT'] = '5434'

from services.users_service.app import app as users_app  # noqa
from shared.db import get_conn

API_PREFIX = f"/api/{os.getenv('API_VERSION','v1')}"


def workload():
    client = users_app.test_client()
    # Register user then login
    client.post(f"{API_PREFIX}/users/register", json={
        'username': 'prof_u', 'email': 'prof_u@example.com', 'password': 'Pass123!'
    })
    client.post(f"{API_PREFIX}/auth/login", json={'username': 'prof_u', 'password': 'Pass123!'})
    client.get(f"{API_PREFIX}/users")


def main():
    prof = cProfile.Profile()
    prof.enable()
    workload()
    prof.disable()
    stats = pstats.Stats(prof).strip_dirs().sort_stats('cumulative')
    stats.print_stats(40)


if __name__ == '__main__':
    main()
