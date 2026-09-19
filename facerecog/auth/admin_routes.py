import hmac
import os
import secrets
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from flask import Blueprint, request, jsonify, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash

from database.postgress import get_connection
from auth.utils import clean_str, public_employee
from auth.decorators import role_required

admin_bp = Blueprint("admin_bp", __name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OTP_VALID_MINUTES = 5
OTP_LENGTH = 6
MAX_OTP_ATTEMPTS = 5  # ilang beses puwedeng magmali bago kailanganin ng bagong OTP

# Saan pupunta ang admin pagkatapos ng tamang OTP.
# Default "/" (dating behavior). Kung may hiwalay kang admin dashboard URL,
# i-set sa .env:  ADMIN_DASHBOARD_URL=/admin
ADMIN_DASHBOARD_URL = os.environ.get("ADMIN_DASHBOARD_URL", "/")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
SMTP_ADMIN = os.environ.get("ADMIN_SMTP_USER")
SMTP_PASSWORD = os.environ.get("ADMIN_SMTP_PASSWORD")


# ---------------------------------------------------------------------------
# Helpers - client IP
# ---------------------------------------------------------------------------
def _get_client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _log_attempt(employee_id_input, stage, success):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO admin_login_attempts (employee_id_input, stage, success, ip_address)
            VALUES (%s, %s, %s, %s)
            """,
            (employee_id_input, stage, success, _get_client_ip())
        )
        conn.commit()
    except Exception as e:
        print(f"[LOG] Could not save login attempt: {e}")
        conn.rollback()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers - OTP generation / sending / DB storage
# ---------------------------------------------------------------------------
def _generate_otp():
    # secrets (hindi random) -- cryptographically secure
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def _save_otp(employee_id, otp_code, purpose):
    """I-invalidate muna ang mga dating unused OTP sa parehong purpose, tapos mag-insert ng bago."""
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE admin_otp_codes
            SET is_used = TRUE
            WHERE employee_id = %s AND purpose = %s AND is_used = FALSE
            """,
            (employee_id, purpose)
        )
        cur.execute(
            """
            INSERT INTO admin_otp_codes (employee_id, otp_code, purpose, expires_at)
            VALUES (%s, %s, %s, %s)
            """,
            (employee_id, otp_code, purpose, expires_at)
        )
        conn.commit()
    finally:
        conn.close()


def _get_active_otp(employee_id, purpose):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, otp_code, attempts, expires_at
            FROM admin_otp_codes
            WHERE employee_id = %s AND purpose = %s AND is_used = FALSE
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (employee_id, purpose)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "otp_code": row[1], "attempts": row[2], "expires_at": row[3]}
    finally:
        conn.close()


def _mark_otp_used(otp_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE admin_otp_codes SET is_used = TRUE WHERE id = %s", (otp_id,))
        conn.commit()
    finally:
        conn.close()


def _increment_otp_attempts(otp_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE admin_otp_codes SET attempts = attempts + 1 WHERE id = %s RETURNING attempts",
            (otp_id,)
        )
        new_attempts = cur.fetchone()[0]
        conn.commit()
        return new_attempts
    finally:
        conn.close()


def _send_otp_email(to_email, otp_code):
    if not SMTP_ADMIN or not SMTP_PASSWORD:
        print(f"[DEV MODE] Admin OTP (email) para sa {to_email}: {otp_code}")
        return

    msg = MIMEText(
        f"Ang iyong Admin Login OTP ay: {otp_code}\n\n"
        f"Valid ito sa loob ng {OTP_VALID_MINUTES} minuto. "
        f"Huwag ibahagi ang code na ito sa iba."
    )
    msg["Subject"] = "Admin Login OTP Verification"
    msg["From"] = SMTP_ADMIN
    msg["To"] = to_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_ADMIN, SMTP_PASSWORD)
        server.sendmail(SMTP_ADMIN, [to_email], msg.as_string())


# ---------------------------------------------------------------------------
# Routes - Admin login page
# ---------------------------------------------------------------------------
@admin_bp.route("/loginadmin")
def loginadmin():
    return render_template("loginadmin.html")


# ---------------------------------------------------------------------------
# Step 1: I-validate ang credentials, magpadala ng EMAIL OTP
# ---------------------------------------------------------------------------
@admin_bp.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}

    employee_id = clean_str(data.get("employee_id"))
    password = data.get("password") or ""

    if not employee_id or not password:
        _log_attempt(employee_id, "credentials", False)
        return jsonify({"error": "Employee ID at password ay required."}), 400

    env_admin_username = os.getenv("ADMIN_USERNAME")
    env_admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
    env_admin_email = os.getenv("ADMIN_EMAIL")

    if not env_admin_username or not env_admin_hash:
        return jsonify({"error": "Admin account ay hindi pa configured sa server."}), 500

    if employee_id != env_admin_username or not check_password_hash(env_admin_hash, password):
        _log_attempt(employee_id, "credentials", False)
        return jsonify({"error": "Invalid credentials."}), 401

    _log_attempt(employee_id, "credentials", True)

    otp_code = _generate_otp()
    _save_otp(employee_id, otp_code, "email")

    # NOTE: hindi na natin ginagamit ang session.clear() dito dahil binubura
    # din nito ang CSRF token na naka-store sa session. Sa halip, alisin
    # lang natin ang mga dating admin-related session keys.
    session.pop("pending_admin_id", None)
    session.pop("pending_admin_stage", None)
    session.pop("employee_id", None)
    session.pop("role", None)

    session["pending_admin_id"] = employee_id
    session["pending_admin_stage"] = "email"

    if env_admin_email:
        try:
            _send_otp_email(env_admin_email, otp_code)
        except Exception as e:
            print(f"[EMAIL] Failed to send admin OTP: {e}")

    return jsonify({
        "success": True,
        "stage": "email",
        "message": "OTP ay naipadala sa iyong email."
    })


# ---------------------------------------------------------------------------
# Step 2: I-verify ang EMAIL OTP — saka lang bigyan ng session
# ---------------------------------------------------------------------------
@admin_bp.route("/api/admin/verify-otp-email", methods=["POST"])
def admin_verify_otp_email():
    data = request.get_json(silent=True) or {}
    otp_input = clean_str(data.get("otp_code"))

    pending_id = session.get("pending_admin_id")
    pending_stage = session.get("pending_admin_stage")

    if not pending_id or pending_stage != "email":
        return jsonify({"error": "Walang pending email verification. Mag-login muli."}), 400

    if not otp_input:
        return jsonify({"error": "OTP code ay required."}), 400

    active_otp = _get_active_otp(pending_id, "email")
    if not active_otp:
        return jsonify({"error": "Walang active na OTP. Mag-login muli."}), 400

    if datetime.utcnow() > active_otp["expires_at"]:
        _mark_otp_used(active_otp["id"])
        session.pop("pending_admin_stage", None)
        _log_attempt(pending_id, "email_otp", False)
        return jsonify({"error": "Na-expire na ang OTP. Mag-login muli."}), 400

    if active_otp["attempts"] >= MAX_OTP_ATTEMPTS:
        _mark_otp_used(active_otp["id"])
        session.pop("pending_admin_stage", None)
        _log_attempt(pending_id, "email_otp", False)
        return jsonify({"error": "Sobra na sa pinapayagang maling attempts. Mag-login muli."}), 400

    if not hmac.compare_digest(otp_input.encode(), active_otp["otp_code"].encode()):
        _increment_otp_attempts(active_otp["id"])
        _log_attempt(pending_id, "email_otp", False)
        return jsonify({"error": "Maling OTP code."}), 400

    # Tama ang email OTP — kumpleto na, bigyan ng session
    _mark_otp_used(active_otp["id"])
    _log_attempt(pending_id, "email_otp", True)

    session.pop("pending_admin_stage", None)
    session.pop("pending_admin_id", None)
    session["employee_id"] = pending_id
    session["role"] = "admin"
    session.permanent = True

    return jsonify({
        "success": True,
        "name": "Administrator",
        "role": "admin",
        "redirect": ADMIN_DASHBOARD_URL,
    })


# ---------------------------------------------------------------------------
# Resend OTP (email lang)
# ---------------------------------------------------------------------------
@admin_bp.route("/api/admin/resend-otp", methods=["POST"])
def admin_resend_otp():
    pending_id = session.get("pending_admin_id")
    pending_stage = session.get("pending_admin_stage")

    if not pending_id or pending_stage != "email":
        return jsonify({"error": "Walang pending admin login. Mag-login muli."}), 400

    otp_code = _generate_otp()
    _save_otp(pending_id, otp_code, "email")

    env_admin_email = os.getenv("ADMIN_EMAIL")
    if env_admin_email:
        try:
            _send_otp_email(env_admin_email, otp_code)
        except Exception as e:
            print(f"[EMAIL] Failed to resend admin OTP: {e}")

    return jsonify({"success": True, "stage": "email", "message": "Bagong OTP ay naipadala na."})


@admin_bp.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Optional: view login attempts (admin-only)
# ---------------------------------------------------------------------------
@admin_bp.route("/api/admin/login-attempts", methods=["GET"])
@role_required("admin")
def get_login_attempts():
    limit = request.args.get("limit", 100, type=int)
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, employee_id_input, stage, success, ip_address, attempted_at
            FROM admin_login_attempts
            ORDER BY attempted_at DESC
            LIMIT %s
            """,
            (limit,)
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()

    for r in rows:
        if r.get("attempted_at"):
            r["attempted_at"] = r["attempted_at"].isoformat()

    return jsonify({"success": True, "attempts": rows})


# ---------------------------------------------------------------------------
# Employee-management routes (walang binago)
# ---------------------------------------------------------------------------

def _row_to_employee(cur, row):
    if row is None:
        return None
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))


@admin_bp.route("/api/admin/employees", methods=["GET"])
@role_required("admin")
def list_employees():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT employee_id, name, contact, address, date_hired,
                   email, role, registered_at
            FROM employees
            ORDER BY name ASC
            """
        )
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        employees = [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()

    return jsonify({"success": True, "employees": employees})


@admin_bp.route("/api/admin/employees/<employee_id>", methods=["GET"])
@role_required("admin")
def get_employee(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT employee_id, name, contact, address, date_hired,
                   email, role, registered_at
            FROM employees WHERE employee_id = %s
            """,
            (employee_id,)
        )
        employee = _row_to_employee(cur, cur.fetchone())
    finally:
        conn.close()

    if not employee:
        return jsonify({"error": "Employee not found."}), 404

    return jsonify({"success": True, "employee": employee})


@admin_bp.route("/api/admin/employees", methods=["POST"])
@role_required("admin")
def create_employee():
    data = request.get_json(silent=True) or {}

    employee_id = clean_str(data.get("employee_id"))
    name = clean_str(data.get("name"))
    contact = clean_str(data.get("contact"))
    address = clean_str(data.get("address"))
    date_hired = data.get("date_hired")
    email = clean_str(data.get("email")).lower()
    role = clean_str(data.get("role")) or "employee"
    password = data.get("password") or ""

    if not employee_id or not name:
        return jsonify({"error": "employee_id at name ay required."}), 400

    if role not in ("admin", "kiosk", "employee"):
        return jsonify({"error": "Invalid role."}), 400

    if password and len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    password_hash = generate_password_hash(password) if password else None
    registered_at = datetime.utcnow() if password else None

    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("SELECT 1 FROM employees WHERE employee_id = %s", (employee_id,))
        if cur.fetchone():
            return jsonify({"error": "Employee ID ay ginagamit na."}), 400

        cur.execute(
            """
            INSERT INTO employees
                (employee_id, name, contact, address, date_hired,
                 email, password_hash, role, registered_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (employee_id, name, contact, address, date_hired,
             email, password_hash, role, registered_at)
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"success": True}), 201


@admin_bp.route("/api/admin/employees/<employee_id>", methods=["PUT"])
@role_required("admin")
def update_employee(employee_id):
    data = request.get_json(silent=True) or {}

    fields = []
    values = []

    for key in ("name", "contact", "address", "email", "role"):
        if key in data:
            value = clean_str(data.get(key))
            if key == "role" and value not in ("admin", "kiosk", "employee"):
                return jsonify({"error": "Invalid role."}), 400
            fields.append(f"{key} = %s")
            values.append(value)

    if "date_hired" in data:
        fields.append("date_hired = %s")
        values.append(data.get("date_hired"))

    if data.get("password") and len(data["password"]) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    if data.get("password"):
        fields.append("password_hash = %s")
        values.append(generate_password_hash(data["password"]))

    if not fields:
        return jsonify({"error": "Walang laman na field para i-update."}), 400

    values.append(employee_id)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE employees SET {', '.join(fields)} WHERE employee_id = %s",
            values
        )
        if cur.rowcount == 0:
            return jsonify({"error": "Employee not found."}), 404
        conn.commit()
    finally:
        conn.close()

    return jsonify({"success": True})


@admin_bp.route("/api/admin/employees/<employee_id>", methods=["DELETE"])
@role_required("admin")
def delete_employee(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE employees
            SET is_deleted = TRUE,
                password_hash = NULL,
                email = NULL,
                registered_at = NULL
            WHERE employee_id = %s
            """,
            (employee_id,)
        )
        if cur.rowcount == 0:
            return jsonify({"error": "Employee not found."}), 404
        conn.commit()
    finally:
        conn.close()

    return jsonify({"success": True})


@admin_bp.route("/api/admin/employees/<employee_id>/restore", methods=["POST"])
@role_required("admin")
def restore_employee(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE employees SET is_deleted = FALSE WHERE employee_id = %s",
            (employee_id,)
        )
        if cur.rowcount == 0:
            return jsonify({"error": "Employee not found."}), 404
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "success": True,
        "message": "Employee restored. Kailangan na nilang mag-register ulit (walang password/email)."
    })