"""Shared error handling utilities for Flask services.

Provides a small APIError exception and standard JSON error responses.
"""

from flask import jsonify
from werkzeug.exceptions import HTTPException


class APIError(Exception):
    """Custom API error with a status code and error code string."""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request", extra: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = int(status)
        self.code = code
        self.extra = extra or {}

    def to_dict(self):
        payload = {
            "code": self.code,
            "message": self.message,
            "status": self.status,
        }
        if self.extra:
            payload["extra"] = self.extra
        return payload


def install_error_handlers(app):
    """Install global error handlers returning standardized JSON."""

    @app.errorhandler(APIError)
    def _handle_api_error(err: APIError):
        return jsonify({"error": err.to_dict()}), err.status

    @app.errorhandler(HTTPException)
    def _handle_http_exception(err: HTTPException):
        payload = {
            "code": (err.name or "http_error").lower().replace(" ", "_"),
            "message": err.description if hasattr(err, 'description') else str(err),
            "status": err.code or 500,
        }
        return jsonify({"error": payload}), err.code or 500

    @app.errorhandler(Exception)
    def _handle_unexpected(err: Exception):
        # Do not leak internals; return generic server error
        payload = {"code": "server_error", "message": "unexpected server error", "status": 500}
        return jsonify({"error": payload}), 500


__all__ = ["APIError", "install_error_handlers"]
