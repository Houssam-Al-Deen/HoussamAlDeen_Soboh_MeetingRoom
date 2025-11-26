"""Circuit breaker wrapper built on `pybreaker`.

Provides a ConditionalCircuitBreaker decorator that applies circuit breaker
protection to HTTP calls.
"""

import pybreaker


class ConditionalCircuitBreaker:
    """Decorator style circuit breaker using `pybreaker`."""
    def __init__(self, fail_max=5, reset_timeout=60, name='ServiceBreaker'):
        self.breaker = pybreaker.CircuitBreaker(
            fail_max=fail_max,
            reset_timeout=reset_timeout,
            name=name,
        )

    def __call__(self, func):
        def wrapper(*args, **kwargs):
            return self.breaker.call(func, *args, **kwargs)
        return wrapper


# Shared breaker instance used for all outbound service calls.
service_breaker = ConditionalCircuitBreaker(
    fail_max=int(pybreaker.CircuitBreaker.FAIL_MAX if hasattr(pybreaker.CircuitBreaker, 'FAIL_MAX') else 5),
    reset_timeout=60,
    name='ServiceBreaker'
)

__all__ = ['ConditionalCircuitBreaker', 'service_breaker']
