from datetime import datetime
import os
 
from flask import Blueprint, request, jsonify, render_template, session
from werkzeug.security import generate_password_hash, check_password_hash
 
from database.postgress import get_connection
from auth.utils import clean_str, public_employee
from auth.decorators import role_required
 
admin_bp = Blueprint("admin_bp", __name__)
 
 

 
@admin_bp.route("/loginadmin")
def loginadmin():
    return render_template("loginadmin.html")
 
 

 
@admin_bp.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
 
    employee_id = clean_str(data.get("employee_id"))
    password = data.get("password") or ""
 
    if not employee_id or not password:
        return jsonify({"error": "Employee ID at password ay required."}), 400
 
 
    env_admin_username = os.getenv("ADMIN_USERNAME")
    env_admin_hash = os.getenv("ADMIN_PASSWORD_HASH")
 
    if not env_admin_username or not env_admin_hash:
        return jsonify({"error": "Admin account ay hindi pa configured sa server."}), 500
 
    if employee_id != env_admin_username or not check_password_hash(env_admin_hash, password):
        return jsonify({"error": "Invalid credentials."}), 401
 
    session.clear()
    session["employee_id"] = employee_id
    session["role"] = "admin"
    session.permanent = True
 
    return jsonify({"success": True, "name": "Administrator", "role": "admin"})
 
 
@admin_bp.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return jsonify({"success": True})
 
 

 
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
        cur.execute("DELETE FROM employees WHERE employee_id = %s", (employee_id,))
        if cur.rowcount == 0:
            return jsonify({"error": "Employee not found."}), 404
        conn.commit()
    finally:
        conn.close()
 
    return jsonify({"success": True})
 