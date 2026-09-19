"""
otp_service/otp_auth.py
Employee registration flow: verify employee_id + email -> send OTP via Gmail
-> verify OTP -> set password -> create account.

Requirements:
    pip install flask psycopg2-binary bcrypt python-dotenv

Environment variables (dapat nasa facerecog/env/.env):
    DATABASE_URL=postgresql://user:password@host:5432/dbname
    GMAIL_ADDRESS=yourcompany@gmail.com
    GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx   # Gmail App Password, HINDI ang normal na password
    SECRET_KEY=some-random-secret-key
"""

import os
import random
import smtplib
import string
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import bcrypt
import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, request

otp_bp = Blueprint('otp', __name__)

DATABASE_URL = os.environ["DATABASE_URL"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]

OTP_LENGTH = 6
OTP_VALID_MINUTES = 5
OTP_RESEND_COOLDOWN_SECONDS = 30


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_otp():
    return "".join(random.choices(string.digits, k=OTP_LENGTH))


def send_otp_email(to_email, name, otp):
    subject = "Cre8ted Travel HRMS — Your verification code"
    body = (
        f"Hi {name},\n\n"
        f"Your Cre8ted Travel HRMS verification code is: {otp}\n"
        f"This code expires in {OTP_VALID_MINUTES} minutes.\n\n"
        f"If you did not request this, you can ignore this email.\n\n"
        f"— Cre8ted Travel HRMS"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


# ---------------------------------------------------------------------------
# STEP 1: Verify employee_id + email, generate + send OTP
# ---------------------------------------------------------------------------

@otp_bp.route("/api/verify-employee", methods=["POST"])
def verify_employee():
    data = request.get_json(silent=True) or {}
    employee_id = (data.get("employee_id") or "").strip()
    email = (data.get("email") or "").strip().lower()

    if not employee_id or not email:
        return jsonify(success=False, error="Employee ID and email are required."), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, employee_id, email, name, account_created
                FROM employees
                WHERE employee_id = %s AND lower(email) = %s
                """,
                (employee_id, email),
            )
            emp = cur.fetchone()

            if not emp:
                return jsonify(success=False, error="Employee ID and email do not match our records."), 404

            if emp["account_created"]:
                return jsonify(success=False, error="An account already exists for this employee. Please sign in."), 409

            otp = generate_otp()
            expires_at = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)

            cur.execute(
                """
                UPDATE employees
                SET otp_code = %s,
                    otp_expires_at = %s,
                    otp_verified = FALSE,
                    otp_last_sent_at = NOW()
                WHERE id = %s
                """,
                (otp, expires_at, emp["id"]),
            )
            conn.commit()

        send_otp_email(email, emp["name"] or employee_id, otp)

        return jsonify(success=True, name=emp["name"] or employee_id)

    except smtplib.SMTPException:
        conn.rollback()
        return jsonify(success=False, error="Could not send verification email. Try again."), 502
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# STEP 1.5: Resend OTP (with basic cooldown)
# ---------------------------------------------------------------------------

@otp_bp.route("/api/resend-otp", methods=["POST"])
def resend_otp():
    data = request.get_json(silent=True) or {}
    employee_id = (data.get("employee_id") or "").strip()
    email = (data.get("email") or "").strip().lower()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, otp_last_sent_at
                FROM employees
                WHERE employee_id = %s AND lower(email) = %s AND account_created = FALSE
                """,
                (employee_id, email),
            )
            emp = cur.fetchone()

            if not emp:
                return jsonify(success=False, error="Employee not found."), 404

            if emp["otp_last_sent_at"]:
                elapsed = (datetime.utcnow() - emp["otp_last_sent_at"]).total_seconds()
                if elapsed < OTP_RESEND_COOLDOWN_SECONDS:
                    wait = int(OTP_RESEND_COOLDOWN_SECONDS - elapsed)
                    return jsonify(success=False, error=f"Please wait {wait}s before resending."), 429

            otp = generate_otp()
            expires_at = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)

            cur.execute(
                """
                UPDATE employees
                SET otp_code = %s, otp_expires_at = %s, otp_verified = FALSE, otp_last_sent_at = NOW()
                WHERE id = %s
                """,
                (otp, expires_at, emp["id"]),
            )
            conn.commit()

        send_otp_email(email, emp["name"] or employee_id, otp)
        return jsonify(success=True)

    except smtplib.SMTPException:
        conn.rollback()
        return jsonify(success=False, error="Could not send verification email. Try again."), 502
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# STEP 2: Verify OTP
# ---------------------------------------------------------------------------

@otp_bp.route("/api/verify-otp", methods=["POST"])
def verify_otp():
    data = request.get_json(silent=True) or {}
    employee_id = (data.get("employee_id") or "").strip()
    email = (data.get("email") or "").strip().lower()
    otp = (data.get("otp") or "").strip()

    if not otp:
        return jsonify(success=False, error="Enter the verification code."), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, otp_code, otp_expires_at
                FROM employees
                WHERE employee_id = %s AND lower(email) = %s AND account_created = FALSE
                """,
                (employee_id, email),
            )
            emp = cur.fetchone()

            if not emp or not emp["otp_code"]:
                return jsonify(success=False, error="No pending verification for this employee."), 404

            if emp["otp_expires_at"] < datetime.utcnow():
                return jsonify(success=False, error="Code expired. Please request a new one."), 410

            if emp["otp_code"] != otp:
                return jsonify(success=False, error="Invalid code."), 401

            cur.execute(
                "UPDATE employees SET otp_verified = TRUE WHERE id = %s",
                (emp["id"],),
            )
            conn.commit()

        return jsonify(success=True)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# STEP 3: Set password / finish registration
# ---------------------------------------------------------------------------

@otp_bp.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    employee_id = (data.get("employee_id") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if len(password) < 8:
        return jsonify(success=False, error="Password must be at least 8 characters."), 400

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, otp_verified, otp_expires_at, account_created
                FROM employees
                WHERE employee_id = %s AND lower(email) = %s
                """,
                (employee_id, email),
            )
            emp = cur.fetchone()

            if not emp:
                return jsonify(success=False, error="Employee not found."), 404

            if emp["account_created"]:
                return jsonify(success=False, error="Account already exists."), 409

            if not emp["otp_verified"] or emp["otp_expires_at"] < datetime.utcnow():
                return jsonify(success=False, error="Email not verified. Please verify OTP again."), 403

            password_hash = hash_password(password)

            cur.execute(
                """
                UPDATE employees
                SET password_hash = %s,
                    account_created = TRUE,
                    otp_code = NULL,
                    otp_expires_at = NULL,
                    otp_verified = FALSE
                WHERE id = %s
                """,
                (password_hash, emp["id"]),
            )
            conn.commit()

        return jsonify(success=True)
    finally:
        conn.close()