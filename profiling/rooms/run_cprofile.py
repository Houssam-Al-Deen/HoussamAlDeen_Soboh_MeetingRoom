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

from services.rooms_service.app import app as rooms_app  # noqa
from shared.db import get_conn

API_PREFIX = f"/api/{os.getenv('API_VERSION','v1')}"


def bootstrap_fixture_data():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM rooms WHERE name='R1'")
    if not cur.fetchone():
        cur.execute("INSERT INTO rooms (name,capacity,equipment,location) VALUES ('R1',4,'TV','L1')")
    cur.execute("SELECT id FROM rooms WHERE name='R2'")
    if not cur.fetchone():
        cur.execute("INSERT INTO rooms (name,capacity,equipment,location) VALUES ('R2',6,'Board','L2')")
    conn.commit(); cur.close(); conn.close()


def workload():
    bootstrap_fixture_data()
    client = rooms_app.test_client()
    client.get(f"{API_PREFIX}/rooms")
    client.get(f"{API_PREFIX}/rooms/1/status")
    client.get(f"{API_PREFIX}/rooms/2/status")


def main():
    prof = cProfile.Profile()
    prof.enable()
    workload()
    prof.disable()
    stats = pstats.Stats(prof).strip_dirs().sort_stats('cumulative')
    stats.print_stats(40)


if __name__ == '__main__':
    main()
