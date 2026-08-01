#api-user.py
import os
import base64
from datetime import datetime
from dotenv import load_dotenv
from flask import Blueprint, request, jsonify, session
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
import gridfs

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")

if not MONGO_URI:
    raise RuntimeError(
        "MONGO_URI is not set. Create a .env file (see .env.example) "
        "with your MongoDB Atlas connection string."
    )

client = MongoClient(MONGO_URI)

try:
    client.admin.command("ping")
    print("MongoDB Atlas Connected!")
except Exception as e:
    print("MongoDB Connection Error:", e)

db = client["employee_db"]

employees_collection = db["employees"]
location_checkins_collection = db["location_checkins"]


fs = gridfs.GridFS(db)



api_bp = Blueprint("api_bp", __name__)



def _clean_str(value):
    return str(value or "").strip()


def get_employee_photo_base64(employee_id):
    """Same lookup qrcodescanner.py uses: pull the employee's registered
    photo straight out of GridFS and return it as a base64 data URL, or
    None if there isn't one on file."""
    photo = fs.find_one({"employee_id": employee_id})
    if photo is None:
        return None
    encoded = base64.b64encode(photo.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _public_employee(doc):
    """I-strip ang sensitive fields bago i-return sa frontend."""
    if not doc:
        return None
    return {
        "employee_id": doc.get("employee_id"),
        "name": doc.get("name"),
        "email": doc.get("email"),
        "role": doc.get("role", "employee"),
        "photo_url": get_employee_photo_base64(doc.get("employee_id")),
    }

@api_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    employee_id = _clean_str(data.get("employee_id"))
    password = data.get("password") or ""

    if not employee_id or not password:
        return jsonify({"error": "QR ID and password are required."}), 400

    employee = employees_collection.find_one({"employee_id": employee_id})

    if not employee or not employee.get("password_hash"):
        return jsonify({"error": "Invalid QR ID or password."}), 401

    if not check_password_hash(employee["password_hash"], password):
        return jsonify({"error": "Invalid QR ID or password."}), 401

    session["employee_id"] = employee.get("employee_id")
    session["email"] = employee.get("email")
    session["role"] = employee.get("role", "employee")

    return jsonify({"success": True, "employee": _public_employee(employee)})


@api_bp.route("/api/verify-employee", methods=["POST"])
def verify_employee():
    data = request.get_json(silent=True) or {}
    employee_id = _clean_str(data.get("employee_id"))
    email = _clean_str(data.get("email")).lower()

    if not employee_id or not email:
        return jsonify({"error": "Employee ID and email are required."}), 400

    employee = employees_collection.find_one({"employee_id": employee_id})

    if not employee:
        return jsonify({"error": "Employee ID not found."}), 404

    on_file_email = _clean_str(employee.get("email")).lower()
    if on_file_email and on_file_email != email:
        return jsonify(
            {"error": "Email does not match our HR records for this Employee ID."}
        ), 400

    if employee.get("password_hash"):
        return jsonify(
            {"error": "This account is already registered. Please sign in instead."}
        ), 400

    return jsonify({"success": True, "name": employee.get("name")})



@api_bp.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    employee_id = _clean_str(data.get("employee_id"))
    email = _clean_str(data.get("email")).lower()
    password = data.get("password") or ""

    if not employee_id or not email or not password:
        return jsonify({"error": "Missing required fields."}), 400

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    employee = employees_collection.find_one({"employee_id": employee_id})
    if not employee:
        return jsonify({"error": "Employee ID not found."}), 404

    if employee.get("password_hash"):
        return jsonify({"error": "This account is already registered."}), 400

    employees_collection.update_one(
        {"employee_id": employee_id},
        {
            "$set": {
                "email": email,
                "password_hash": generate_password_hash(password),
                "registered_at": datetime.utcnow(),
            }
        },
    )

    return jsonify({"success": True})


@api_bp.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@api_bp.route("/api/me", methods=["GET"])
def me():
    employee_id = session.get("employee_id")
    if not employee_id:
        return jsonify({"error": "Not signed in."}), 401

    employee = employees_collection.find_one({"employee_id": employee_id})
    if not employee:
        return jsonify({"error": "Employee record not found."}), 404

    return jsonify({"success": True, "employee": _public_employee(employee)})



@api_bp.route("/api/userback", methods=["POST"])
def create_location_checkin():
    data = request.get_json(silent=True) or {}

    employee_id = _clean_str(data.get("employee_id"))
    scan_id = _clean_str(data.get("scan_id"))
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy")
    timestamp = data.get("timestamp") or datetime.utcnow().isoformat() + "Z"

    if lat is None or lng is None:
        return jsonify({"error": "Missing location coordinates."}), 400

    employee = employees_collection.find_one({"employee_id": employee_id}) if employee_id else None
    employee_name = employee.get("name") if employee else ""

    record = {
        "employee_id": employee_id,
        "employee_name": employee_name,
        "scan_id": scan_id,
        "lat": lat,
        "lng": lng,
        "accuracy": accuracy,
        "timestamp": timestamp,
        "created_at": datetime.utcnow(),
    }
    location_checkins_collection.insert_one(record)

    return jsonify(
        {
            "success": True,
            "employeeName": employee_name,
            "message": "Attendance recorded.",
            "lat": lat,
            "lng": lng,
        }
    )