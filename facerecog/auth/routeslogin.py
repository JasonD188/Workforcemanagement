
from datetime import datetime

from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

from database.postgress import get_connection
from auth.utils import clean_str, public_employee
from auth.decorators import login_required
from flask import render_template


auth_bp = Blueprint("auth_bp", __name__)


def _find_employee(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT employee_id, name, contact, address, date_hired,
                   email, password_hash, role, registered_at, photo_file_id
            FROM employees WHERE employee_id = %s
            """,
            (employee_id,)
        )
        row = cur.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cur.description]
        return dict(zip(columns, row))
    finally:
        conn.close()


@auth_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    employee_id = clean_str(data.get("employee_id"))
    password = data.get("password") or ""

    if not employee_id or not password:
        return jsonify({"error": "QR ID and password are required."}), 400

    employee = _find_employee(employee_id)

    if not employee or not employee.get("password_hash"):
        return jsonify({"error": "Invalid QR ID or password."}), 401

    if not check_password_hash(employee["password_hash"], password):
        return jsonify({"error": "Invalid QR ID or password."}), 401

    session["employee_id"] = employee.get("employee_id")
    session["email"] = employee.get("email")
    session["role"] = employee.get("role", "employee")

    return jsonify({"success": True, "employee": public_employee(employee)})


@auth_bp.route("/api/verify-employee", methods=["POST"])
def verify_employee():
    data = request.get_json(silent=True) or {}
    employee_id = clean_str(data.get("employee_id"))
    email = clean_str(data.get("email")).lower()

    if not employee_id or not email:
        return jsonify({"error": "Employee ID and email are required."}), 400

    employee = _find_employee(employee_id)

    if not employee:
        return jsonify({"error": "Employee ID not found."}), 404

    on_file_email = clean_str(employee.get("email")).lower()
    if on_file_email and on_file_email != email:
        return jsonify(
            {"error": "Email does not match our HR records for this Employee ID."}
        ), 400

    if employee.get("password_hash"):
        return jsonify(
            {"error": "This account is already registered. Please sign in instead."}
        ), 400

    return jsonify({"success": True, "name": employee.get("name")})


@auth_bp.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    employee_id = clean_str(data.get("employee_id"))
    email = clean_str(data.get("email")).lower()
    password = data.get("password") or ""

    if not employee_id or not email or not password:
        return jsonify({"error": "Missing required fields."}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    employee = _find_employee(employee_id)
    if not employee:
        return jsonify({"error": "Employee ID not found."}), 404

    if employee.get("password_hash"):
        return jsonify({"error": "This account is already registered."}), 400

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE employees
            SET email = %s, password_hash = %s, registered_at = %s
            WHERE employee_id = %s
            """,
            (email, generate_password_hash(password), datetime.utcnow(), employee_id)
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"success": True})


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@auth_bp.route("/api/me", methods=["GET"])
@login_required
def me():
    employee_id = session.get("employee_id")
    employee = _find_employee(employee_id)
    if not employee:
        return jsonify({"error": "Employee record not found."}), 404

    return jsonify({"success": True, "employee": public_employee(employee)})
