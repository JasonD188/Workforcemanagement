
from functools import wraps
from flask import session, jsonify


def login_required(f):
    """Require any signed-in employee (admin, kiosk, or regular employee)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "employee_id" not in session:
            return jsonify({"error": "Not signed in."}), 401
        return f(*args, **kwargs)
    return wrapper


def role_required(*allowed_roles):
    """Require a signed-in employee whose role is in allowed_roles.

    Usage:
        @role_required('admin')
        @role_required('admin', 'kiosk')
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "employee_id" not in session:
                return jsonify({"error": "Not signed in."}), 401
            if session.get("role") not in allowed_roles:
                return jsonify({"error": "Forbidden."}), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator