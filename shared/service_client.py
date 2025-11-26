import os
import requests
import pybreaker
from shared.errors import APIError
from shared.circuit_breaker import service_breaker

API_VERSION = os.getenv('API_VERSION', 'v1')
# Service URLs - default to docker names, override for local testing
USERS_SERVICE_URL = os.getenv('USERS_SERVICE_URL', 'http://users_service:8000')
ROOMS_SERVICE_URL = os.getenv('ROOMS_SERVICE_URL', 'http://rooms_service:8002')
BOOKINGS_SERVICE_URL = os.getenv('BOOKINGS_SERVICE_URL', 'http://bookings_service:8003')
REVIEWS_SERVICE_URL = os.getenv('REVIEWS_SERVICE_URL', 'http://reviews_service:8004')

@service_breaker
def _http_get(url: str, timeout: float = 1.0):
    return requests.get(url, timeout=timeout)

def _call(url: str):
    """Make HTTP call with circuit breaker protection."""
    try:
        return _http_get(url)
    except (pybreaker.CircuitBreakerError, requests.RequestException):
        raise APIError('dependency unavailable', status=503, code='service_unavailable')

def ensure_user_exists(user_id: int):
    url = f"{USERS_SERVICE_URL}/api/{API_VERSION}/users/id/{user_id}/status"
    resp = _call(url)
    if resp.status_code == 404:
        raise APIError('user not found', status=404, code='not_found')
    if resp.status_code < 200 or resp.status_code >= 300:
        raise APIError('dependency unavailable', status=503, code='service_unavailable')

def ensure_room_exists(room_id: int):
    url = f"{ROOMS_SERVICE_URL}/api/{API_VERSION}/rooms/{room_id}/status"
    resp = _call(url)
    if resp.status_code == 404:
        raise APIError('room not found', status=404, code='not_found')
    if resp.status_code < 200 or resp.status_code >= 300:
        raise APIError('dependency unavailable', status=503, code='service_unavailable')

def get_user_basic(user_id: int):
    url = f"{USERS_SERVICE_URL}/api/{API_VERSION}/users/id/{user_id}/status"
    resp = _call(url)
    if resp.status_code == 200:
        return resp.json()
    return {'id': user_id, 'status': 'unknown'}

def get_room_basic(room_id: int):
    url = f"{ROOMS_SERVICE_URL}/api/{API_VERSION}/rooms/{room_id}/status"
    resp = _call(url)
    if resp.status_code == 200:
        return resp.json()
    return {'room_id': room_id, 'status': 'unknown'}

def get_room_active_status(room_id: int):
    url = f"{BOOKINGS_SERVICE_URL}/api/{API_VERSION}/bookings/room/{room_id}/active-status"
    resp = _call(url)
    if resp.status_code == 200:
        return resp.json()
    raise APIError('dependency unavailable', status=503, code='service_unavailable')

__all__ = ['ensure_user_exists', 'ensure_room_exists', 'get_user_basic', 'get_room_basic', 'get_room_active_status']
