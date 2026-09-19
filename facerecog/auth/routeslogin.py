import os
import random
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash

from database.postgress import get_connection
from auth.utils import clean_str, public_employee
from auth.decorators import login_required


auth_bp = Blueprint("auth_bp", __name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OTP_VALID_MINUTES = 5          # ilang minuto bago mag-expire ang OTP
OTP_LENGTH = 6                 # bilang ng digits ng OTP

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_USER = os.environ.get("SMTP_USER")          # e.g. yourapp@gmail.com
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")  # app password, hindi regular password


# ---------------------------------------------------------------------------
# Helpers - Employee lookup
# ---------------------------------------------------------------------------
def _find_employee(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, employee_id, name, contact, address, date_hired,
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


# ---------------------------------------------------------------------------
# Helpers - OTP
# ---------------------------------------------------------------------------
def _generate_otp():
    return "".join(random.choices("0123456789", k=OTP_LENGTH))


def _save_otp(employee_pk_id, otp_code, purpose="registration"):
    """employee_pk_id = employees.id (integer), hindi employees.employee_id (varchar)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE otp_codes
            SET is_used = TRUE
            WHERE employee_id = %s AND purpose = %s AND is_used = FALSE
            """,
            (employee_pk_id, purpose)
        )

        expires_at = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)
        cur.execute(
            """
            INSERT INTO otp_codes (employee_id, otp_code, purpose, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (employee_pk_id, otp_code, purpose, expires_at)
        )
        conn.commit()
    finally:
        conn.close()


def _verify_otp(employee_pk_id, otp_code, purpose="registration"):
    """Nire-return ang True/False, at ginagawang used ang OTP kapag tama."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, expires_at, is_used
            FROM otp_codes
            WHERE employee_id = %s AND otp_code = %s AND purpose = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (employee_pk_id, otp_code, purpose)
        )
        row = cur.fetchone()
        if row is None:
            return False, "Invalid OTP code."

        otp_id, expires_at, is_used = row

        if is_used:
            return False, "This OTP has already been used."

        if datetime.utcnow() > expires_at:
            return False, "This OTP has expired. Please request a new one."

        cur.execute("UPDATE otp_codes SET is_used = TRUE WHERE id = %s", (otp_id,))
        conn.commit()
        return True, None
    finally:
        conn.close()


def _send_otp_email(to_email, otp_code):
    if not SMTP_USER or not SMTP_PASSWORD:
        print(f"[DEV MODE] OTP para sa {to_email}: {otp_code}")
        return

    msg = MIMEText(
        f"Ang iyong OTP code ay: {otp_code}\n\n"
        f"Valid ito sa loob ng {OTP_VALID_MINUTES} minuto. "
        f"Huwag ibahagi ang code na ito sa iba."
    )
    msg["Subject"] = "Iyong OTP Verification Code"
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, [to_email], msg.as_string())


# ---------------------------------------------------------------------------
# Routes - Login / Register
# ---------------------------------------------------------------------------
@auth_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    employee_id = clean_str(data.get("employee_id"))
    password = data.get("password") or ""

    print(f"[DEBUG] received employee_id={employee_id!r}, password_len={len(password)}")

    if not employee_id or not password:
        return jsonify({"error": "QR ID and password are required."}), 400

    employee = _find_employee(employee_id)
    print(f"[DEBUG] employee found: {employee is not None}")

    if not employee or not employee.get("password_hash"):
        print(f"[DEBUG] no employee or no password_hash. employee={employee}")
        return jsonify({"error": "Invalid QR ID or password."}), 401

    print(f"[DEBUG] stored hash: {employee['password_hash']!r}")
    match = check_password_hash(employee["password_hash"], password)
    print(f"[DEBUG] password match: {match}")

    if not match:
        return jsonify({"error": "Invalid QR ID or password."}), 401

    session["employee_id"] = employee.get("employee_id")
    session["email"] = employee.get("email")
    session["role"] = employee.get("role", "employee")

    return jsonify({"success": True, "employee": public_employee(employee)})


@auth_bp.route("/api/verify-employee", methods=["POST"])
def verify_employee():
    """Step 1: i-verify ang employee_id + email, tapos mag-generate at magpadala ng OTP."""
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

    otp_code = _generate_otp()
    _save_otp(employee["id"], otp_code, purpose="registration")
    _send_otp_email(email, otp_code)

    session["pending_employee_id"] = employee_id
    session["pending_email"] = email

    return jsonify({"success": True, "name": employee.get("name")})


@auth_bp.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    """Step 2: i-verify ang OTP code na ipinasok ng user."""
    data = request.get_json(silent=True) or {}
    otp_code = clean_str(data.get("otp_code"))

    employee_id = session.get("pending_employee_id")
    if not employee_id:
        return jsonify({"error": "No pending verification found. Please start again."}), 400

    if not otp_code:
        return jsonify({"error": "OTP code is required."}), 400

    employee = _find_employee(employee_id)
    if not employee:
        return jsonify({"error": "Employee ID not found."}), 404

    is_valid, error_message = _verify_otp(employee["id"], otp_code, purpose="registration")
    if not is_valid:
        return jsonify({"error": error_message}), 400

    return jsonify({"success": True})


@auth_bp.route("/api/resend-otp", methods=["POST"])
def resend_otp():
    """Muling magpadala ng bagong OTP kung na-expire o nawala ang dati."""
    employee_id = session.get("pending_employee_id")
    email = session.get("pending_email")

    if not employee_id or not email:
        return jsonify({"error": "No pending verification found. Please start again."}), 400

    employee = _find_employee(employee_id)
    if not employee:
        return jsonify({"error": "Employee ID not found."}), 404

    otp_code = _generate_otp()
    _save_otp(employee["id"], otp_code, purpose="registration")
    _send_otp_email(email, otp_code)

    return jsonify({"success": True})


@auth_bp.route("/api/register", methods=["POST"])
def register():
    """Step 3: kumpletuhin ang registration matapos ma-verify ang OTP."""
    data = request.get_json(silent=True) or {}
    employee_id = clean_str(data.get("employee_id"))
    email = clean_str(data.get("email")).lower()
    password = data.get("password") or ""

    if not employee_id or not email or not password:
        return jsonify({"error": "Missing required fields."}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    if session.get("pending_employee_id") != employee_id:
        return jsonify({"error": "Please verify your OTP before registering."}), 400

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

    session.pop("pending_employee_id", None)
    session.pop("pending_email", None)

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