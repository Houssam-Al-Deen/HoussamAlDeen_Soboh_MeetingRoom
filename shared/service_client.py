"""Shared service client utilities.

This module centralizes outbound HTTP calls between microservices and applies
the global circuit breaker to those calls. It also exposes small helpers to
validate existence of users and rooms and to fetch basic info.

Environment variables:
- ``API_VERSION``: API prefix version (default: ``v1``)
- ``USERS_SERVICE_URL``: Base URL for users service (default: docker name)
- ``ROOMS_SERVICE_URL``: Base URL for rooms service (default: docker name)
- ``BOOKINGS_SERVICE_URL``: Base URL for bookings service (default: docker name)
- ``REVIEWS_SERVICE_URL``: Base URL for reviews service (default: docker name)
"""

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
    """Perform a GET request with circuit breaker protection.

    :param url: Absolute URL to request.
    :param timeout: Timeout in seconds (default: 1.0).
    :returns: ``requests.Response`` on success.
    :raises requests.RequestException: On low-level request failure.
    """
    return requests.get(url, timeout=timeout)

def _call(url: str):
    """Call ``_http_get`` and normalize failures to ``APIError(503)``.

    Converts breaker opens and request exceptions into a standardized
    ``service_unavailable`` API error.

    :param url: Absolute URL to request.
    :returns: ``requests.Response`` when successful.
    :raises APIError: With status 503 when dependency is unavailable.
    """
    try:
        return _http_get(url)
    except (pybreaker.CircuitBreakerError, requests.RequestException):
        raise APIError('dependency unavailable', status=503, code='service_unavailable')

def ensure_user_exists(user_id: int):
    """Validate that a user exists via the users service.

    :param user_id: User identifier.
    :raises APIError: 404 if missing, 503 if dependency unavailable.
    """
    url = f"{USERS_SERVICE_URL}/api/{API_VERSION}/users/id/{user_id}/status"
    resp = _call(url)
    if resp.status_code == 404:
        raise APIError('user not found', status=404, code='not_found')
    if resp.status_code < 200 or resp.status_code >= 300:
        raise APIError('dependency unavailable', status=503, code='service_unavailable')

def ensure_room_exists(room_id: int):
    """Validate that a room exists via the rooms service.

    :param room_id: Room identifier.
    :raises APIError: 404 if missing, 503 if dependency unavailable.
    """
    url = f"{ROOMS_SERVICE_URL}/api/{API_VERSION}/rooms/{room_id}/status"
    resp = _call(url)
    if resp.status_code == 404:
        raise APIError('room not found', status=404, code='not_found')
    if resp.status_code < 200 or resp.status_code >= 300:
        raise APIError('dependency unavailable', status=503, code='service_unavailable')

def get_user_basic(user_id: int):
    """Fetch minimal user info; returns fallback on failure.

    :param user_id: User identifier.
    :returns: Dict with basic user data or fallback status.
    """
    url = f"{USERS_SERVICE_URL}/api/{API_VERSION}/users/id/{user_id}/status"
    resp = _call(url)
    if resp.status_code == 200:
        return resp.json()
    return {'id': user_id, 'status': 'unknown'}

def get_room_basic(room_id: int):
    """Fetch minimal room info; returns fallback on failure.

    :param room_id: Room identifier.
    :returns: Dict with basic room data or fallback status.
    """
    url = f"{ROOMS_SERVICE_URL}/api/{API_VERSION}/rooms/{room_id}/status"
    resp = _call(url)
    if resp.status_code == 200:
        return resp.json()
    return {'room_id': room_id, 'status': 'unknown'}

def get_room_active_status(room_id: int):
    """Return current active status for a room via bookings service.

    :param room_id: Room identifier.
    :returns: Dict with ``status`` in {'booked','available'}.
    :raises APIError: 503 when dependency unavailable.
    """
    url = f"{BOOKINGS_SERVICE_URL}/api/{API_VERSION}/bookings/room/{room_id}/active-status"
    resp = _call(url)
    if resp.status_code == 200:
        return resp.json()
    raise APIError('dependency unavailable', status=503, code='service_unavailable')

__all__ = ['ensure_user_exists', 'ensure_room_exists', 'get_user_basic', 'get_room_basic', 'get_room_active_status']
