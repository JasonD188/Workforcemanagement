from functools import wraps
from flask import session, jsonify, abort


def login_required(f):
    """Require any signed-in employee (admin, kiosk, or regular employee).
    Ginagamit ito sa API endpoints — nagre-return ng JSON 401 kung walang session.
    """
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


def page_login_required(f):
    """Require any signed-in employee.
    Ginagamit ito sa mga routes na nagse-serve ng HTML PAGE (hindi API) —
    tulad ng /user_dashboard. Kapag walang session, mag-404 imbes na JSON,
    dahil hindi natin gustong malaman ng hindi naka-login na user na
    umiiral ang route na ito.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "employee_id" not in session:
            abort(404)
        return f(*args, **kwargs)
    return wrapper


def page_role_required(*allowed_roles):
    """Require a signed-in employee with a specific role, para sa HTML page routes
    (hal. /admin_dashboard na dapat lang makita ng admin).

    Usage:
        @page_role_required('admin')
        @page_role_required('admin', 'kiosk')
    """
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if "employee_id" not in session:
                abort(404)
            if session.get("role") not in allowed_roles:
                abort(404)
            return f(*args, **kwargs)
        return wrapper
    return decorator