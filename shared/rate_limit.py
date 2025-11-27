import os
import time
import threading
from collections import deque
from typing import Callable, Optional

from flask import request

try:
    from .errors import APIError
except Exception:  # pragma: no cover - fallback for docs/import path adjustments
    from shared.errors import APIError


class _RateLimiter:
    """Simple in-memory sliding-window rate limiter.

    Not suitable for multi-process or distributed deployments without
    external storage. Intended as a lightweight default.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._hits = {}  # key -> deque[timestamps]

    def hit(self, key: str, max_calls: int, period_sec: int) -> bool:
        now = time.monotonic()
        cutoff = now - period_sec
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            # drop old entries
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= max_calls:
                return False
            dq.append(now)
            return True


_limiter = _RateLimiter()


def _client_ip() -> str:
    # Prefer X-Forwarded-For when present (use first hop)
    fwd = request.headers.get('X-Forwarded-For')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def rate_limit(max_calls: int, period_sec: int, key: Optional[str | Callable[[], str]] = 'ip'):
    """Decorator to rate-limit a route.

    - key='ip': limits per client IP
    - key='user': limits per authenticated user id (falls back to IP if missing)
    - key=callable: custom function returning a string key

    Always enabled.
    """
    def deco(fn):
        import functools

        def _compute_key():
            ep = (getattr(request, 'endpoint', None) or request.path or 'unknown')
            if callable(key):
                try:
                    return f"{ep}:{str(key())}"
                except Exception:
                    return f"{ep}:ip:{_client_ip()}"
            if key == 'user':
                uid = getattr(request, '_auth', {}) and getattr(request, '_auth', {}).get('sub')
                return f"{ep}:user:{uid}" if uid is not None else f"{ep}:ip:{_client_ip()}"
            # default ip
            return f"{ep}:ip:{_client_ip()}"

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            # Respect env toggle so tests can control activation.
            if os.getenv('RATE_LIMIT_ENABLED', '0') != '1':
                return fn(*a, **kw)
            k = _compute_key()
            allowed = _limiter.hit(k, max_calls, period_sec)
            if not allowed:
                raise APIError('too many requests', status=429, code='rate_limited')
            return fn(*a, **kw)
        return wrapper

    return deco


def reset_rate_limiter():
    """Testing helper: clear in-memory counters.

    Not thread-safe across concurrent requests; intended for unit tests only.
    """
    with _limiter._lock:  # type: ignore[attr-defined]
        _limiter._hits.clear()
