# api.py
from flask import Blueprint, jsonify, request, session
from datetime import datetime, timedelta, timezone
import os
from pymongo import MongoClient, ReturnDocument
from gridfs import GridFS
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
import base64


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

fs = GridFS(db, collection="face_photos")

scans_collection = db["scan_logs"]
location_checkins_collection = db["location_checkins"]

schedules_collection = db["schedules"]
leaves_collection = db["leaves"]
quick_add_collection = db["quick_add_employees"]
counters_collection = db["counters"]


def _next_id(counter_name):
    """Atomic, cross-process auto-increment counter gamit ang MongoDB's
    find_one_and_update - ligtas ito kahit maraming Gunicorn workers ang
    sabay-sabay na gumagawa ng schedule/leave (hindi katulad ng dating
    plain Python int variable na hiwa-hiwalay per-worker)."""
    doc = counters_collection.find_one_and_update(
        {"_id": counter_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


def load_employees():
    """Load all employee records from MongoDB Atlas, keyed by employee_id."""
    employees = {}
    for doc in employees_collection.find():
        eid = doc.get("employee_id")
        if not eid:
            continue
        employees[eid] = {
            "name": doc.get("name", ""),
            "contact": doc.get("contact", ""),
            "address": doc.get("address", ""),
            "date_hired": doc.get("date_hired", "")
        }
    return employees


def find_employee_by_name(person_name):
    employees = load_employees()

    matches = [
        (eid, info) for eid, info in employees.items()
        if info.get("name", "").strip().lower() == person_name.strip().lower()
    ]

    if len(matches) > 1:
        print(f"WARNING: {len(matches)} MongoDB employee records share the name "
              f"{person_name!r}: {[m[0] for m in matches]}. "
              f"Returning the first one - results may be inconsistent "
              f"until duplicates are removed.")

    if matches:
        return matches[0]

    available_names = [info.get("name") for info in employees.values()]
    print(f"No MongoDB record found for face-match name {person_name!r}. "
          f"Available employee names: {available_names}")

    return None, None


def log_verified_scan(employee_id, name, score, scanned_b64, registered_b64,
                       scan_type=None, qr_code_image=None, status=None, note=None):
    verified_at = datetime.utcnow()
    inserted_id = None

    try:
        result = scans_collection.insert_one({
            "employee_id": employee_id,
            "name": name,
            "score": score,
            "scanned_image": scanned_b64,
            "registered_image": registered_b64,
            "scan_type": scan_type,
            "qr_code_image": qr_code_image,
            "status": status,
            "note": note,
            "verified_at": verified_at
        })
        inserted_id = str(result.inserted_id)
    except Exception as log_err:
        print("Could not save scan log to MongoDB:", log_err)

    return verified_at, inserted_id


def fetch_recent_scans(limit=200):
    """Raw scan_logs documents from MongoDB, newest first."""
    return list(scans_collection.find().sort("verified_at", -1).limit(limit))


def _clean_str(value):
    return str(value or "").strip()



api_bp = Blueprint("api", __name__)



LOCATION_MATCH_WINDOW_MINUTES = 15

_notifications = [
    {"id": 1, "title": "Welcome", "body": "Dashboard connected.", "time": "just now", "read": False},
]


def _quick_add_qr_id(name):
    """
    Stable placeholder ID para sa mga "quick-added" na pangalan sa
    dashboard (walang totoong registration/QR pa via /registerface).
    Malinaw na naka-prefix ng QUICK- para hindi ito magkamali/malito sa
    totoong MongoDB employee_id (8-char uuid hex) na ginagawa ng
    registerface.py. Kapag na-register na ang taong ito nang tunay via
    /registerface, ang totoong employee_id na mula MongoDB ang gagamitin
    sa lahat ng dako - hindi na itong placeholder.
    """
    slug = "".join(ch for ch in name.upper() if ch.isalnum())[:6] or "EMP"
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return f"QUICK-{slug}-{(h % 9000) + 1000}"


def _is_fully_verified(scan):
    """
    True lang kapag: (1) face-verified ang scan (score >= threshold), AT
    (2) may location check-in na naitala para dito.
    """
    from deepfacerecog import MIN_ACCESS_SCORE_PERCENT

    if (scan.get("score") or 0) < MIN_ACCESS_SCORE_PERCENT:
        return False

    scan_id = str(scan.get("_id") or "")

    if scan_id:
        match = location_checkins_collection.find_one({"scan_id": scan_id})
        if match is not None:
            return True

    verified_at = scan.get("verified_at")
    if not verified_at:
        return False

    verified_at_utc = (
        verified_at if verified_at.tzinfo is None
        else verified_at.astimezone(timezone.utc).replace(tzinfo=None)
    )

    window_end = verified_at_utc + timedelta(minutes=LOCATION_MATCH_WINDOW_MINUTES)
    match = location_checkins_collection.find_one({
        "employee_id": scan.get("employee_id"),
        "created_at": {"$gte": verified_at_utc, "$lte": window_end},
    })
    return match is not None


# Employees
def _get_employee_photo_data_url(employee_id):
    """Fetch this employee's current photo from GridFS and return it as a
    base64 data URL, or '' if none is stored."""
    try:
        grid_file = fs.find_one({"employee_id": employee_id})
        if grid_file is None:
            return ""
        jpeg_bytes = grid_file.read()
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        content_type = grid_file.content_type or "image/jpeg"
        return f"data:{content_type};base64,{b64}"
    except Exception as e:
        print(f"Could not load photo from GridFS for {employee_id}:", e)
        return ""


def _public_employee(doc):
    """Strip sensitive fields before returning to frontend (used by the
    auth routes below)."""
    if not doc:
        return None
    employee_id = doc.get("employee_id")
    return {
        "employee_id": employee_id,
        "name": doc.get("name"),
        "email": doc.get("email"),
        "role": doc.get("role", "employee"),
        "photo_url": _get_employee_photo_data_url(employee_id) or None,
    }


#Auth (sign in / register / session) 
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


@api_bp.route("/api/employees", methods=["GET"])
def get_employees():
    """
    Isinasama ang "photo" (base64 data-URL string) mula sa employee
    document, kung meron, plus ang mga "quick-added" na pangalan (na wala
    pang totoong QR/face registration) - ngayon mula na sa MongoDB
    (quick_add_collection) imbes na sa in-memory list, kaya laging tugma
    ang makikita saan mang worker/request.
    """
    roster = []
    seen_names = set()

    for doc in employees_collection.find():
        name = doc.get("name")
        employee_id = doc.get("employee_id")
        if not name or not employee_id:
            continue
        roster.append({
            "name": name,
            "employeeId": employee_id,
            "contact": doc.get("contact", ""),
            "address": doc.get("address", ""),
            "dateHired": doc.get("date_hired", ""),
            "photo": _get_employee_photo_data_url(employee_id),
            "source": "registered",
        })
        seen_names.add(name)

    for doc in quick_add_collection.find():
        name = doc.get("name")
        if not name or name in seen_names:
            continue
        roster.append({
            "name": name,
            "employeeId": doc.get("employeeId") or _quick_add_qr_id(name),
            "contact": "",
            "address": "",
            "dateHired": "",
            "photo": "",
            "source": "quick_add",
        })
        seen_names.add(name)

    roster.sort(key=lambda e: e["name"].lower())
    names = [e["name"] for e in roster]

    return jsonify({"employees": names, "roster": roster})


@api_bp.route("/api/employees", methods=["POST"])
def add_employee():
    """
    Quick-add ng pangalan lang (walang face/QR) mula sa dashboard UI
    (hal. sa loob ng Schedule/Leave modal). Ang mga taong ito ay dapat
    pa ring i-register nang tunay via /registerface kapag available na,
    para magkaroon sila ng totoong QR ID at ma-verify sa Monitoring.
    """
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400

    already_registered = employees_collection.find_one({
        "name": {"$regex": f"^{name}$", "$options": "i"}
    })
    if already_registered:
        return jsonify({"error": f"{name} is already registered via Register Employee."}), 400

    already_quick_added = quick_add_collection.find_one({
        "name": {"$regex": f"^{name}$", "$options": "i"}
    })
    if not already_quick_added:
        quick_add_collection.insert_one({
            "name": name,
            "employeeId": _quick_add_qr_id(name),
            "created_at": datetime.utcnow(),
        })
    return get_employees()


# ---------- Schedules ----------
def _schedule_public(doc):
    """Strip Mongo's internal _id before returning to the frontend - the
    frontend uses the custom integer "id" field (from _next_id) instead."""
    if not doc:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


@api_bp.route("/api/schedules", methods=["GET"])
def get_schedules():
    docs = list(schedules_collection.find({}, {"_id": 0}).sort("id", 1))
    return jsonify({"schedules": docs})


@api_bp.route("/api/schedules", methods=["POST"])
def create_schedule():
    data = request.get_json() or {}
    shift_name = (data.get("shiftName") or "").strip()
    date = (data.get("date") or "").strip()
    time = (data.get("time") or "").strip()
    time_in = (data.get("timeIn") or "").strip()
    time_out = (data.get("timeOut") or "").strip()
    location = (data.get("location") or "").strip()
    shift_type = (data.get("type") or "schedule").strip()
    employee = (data.get("employee") or "").strip()
    employee_qr_id = (data.get("employeeQrId") or "").strip()
    replacing_employee = (data.get("replacingEmployee") or "").strip() or None
    replacing_employee_qr_id = (data.get("replacingEmployeeQrId") or "").strip() or None
    lat = data.get("lat")
    lng = data.get("lng")

    if not (shift_name and date and time and employee):
        return jsonify({"error": "Missing shift details."}), 400

    shift = {
        "id": _next_id("schedule"),
        "shiftName": shift_name,
        "date": date,
        "time": time,
        "timeIn": time_in,
        "timeOut": time_out,
        "location": location,
        "type": shift_type,
        "employees": [employee],
        "employeeQrId": employee_qr_id,
        "replacingEmployee": replacing_employee,
        "replacingEmployeeQrId": replacing_employee_qr_id,
        "lat": lat,
        "lng": lng,
        "created_at": datetime.utcnow(),
    }
    schedules_collection.insert_one(shift)

    docs = list(schedules_collection.find({}, {"_id": 0}).sort("id", 1))
    return jsonify({"schedules": docs, "shift": _schedule_public(shift)})


@api_bp.route("/api/schedules/<int:schedule_id>/assign", methods=["POST"])
def assign_schedule(schedule_id):
    data = request.get_json() or {}
    employee = (data.get("employee") or "").strip()
    if not employee:
        return jsonify({"error": "Employee is required."}), 400

    shift = schedules_collection.find_one_and_update(
        {"id": schedule_id},
        {"$addToSet": {"employees": employee}},
        return_document=ReturnDocument.AFTER,
    )
    if not shift:
        return jsonify({"error": "Shift not found."}), 404

    docs = list(schedules_collection.find({}, {"_id": 0}).sort("id", 1))
    return jsonify({"schedules": docs, "shift": _schedule_public(shift)})


# ---------- Leaves ----------
@api_bp.route("/api/leaves", methods=["GET"])
def get_leaves():
    docs = list(leaves_collection.find({}, {"_id": 0}).sort("id", 1))
    return jsonify({"leaves": docs})


@api_bp.route("/api/leaves", methods=["POST"])
def create_leave():
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    dates = (data.get("dates") or "").strip()
    qr_id = (data.get("qrId") or "").strip()
    if not (name and dates):
        return jsonify({"error": "Name and dates are required."}), 400

    leave = {
        "id": _next_id("leave"),
        "name": name,
        "dates": dates,
        "qrId": qr_id,
        "status": "Pending",
        "createdAt": datetime.now().isoformat(),
    }
    leaves_collection.insert_one(leave)
    return jsonify({"leave": _schedule_public(leave)})


def _set_leave_status(leave_id, status):
    leave = leaves_collection.find_one_and_update(
        {"id": leave_id},
        {"$set": {"status": status, "processedAt": datetime.now().isoformat()}},
        return_document=ReturnDocument.AFTER,
    )
    return _schedule_public(leave) if leave else None


@api_bp.route("/api/leaves/<int:leave_id>/approve", methods=["POST"])
def approve_leave(leave_id):
    leave = _set_leave_status(leave_id, "Approved")
    if not leave:
        return jsonify({"error": "Leave request not found."}), 404
    return jsonify({"leave": leave})


@api_bp.route("/api/leaves/<int:leave_id>/decline", methods=["POST"])
def decline_leave(leave_id):
    leave = _set_leave_status(leave_id, "Declined")
    if not leave:
        return jsonify({"error": "Leave request not found."}), 404
    return jsonify({"leave": leave})


#  Notifications
@api_bp.route("/api/notifications", methods=["GET"])
def get_notifications():
    return jsonify({"notifications": _notifications})


@api_bp.route("/api/notifications/mark-all-read", methods=["POST"])
def mark_all_read():
    for n in _notifications:
        n["read"] = True
    return jsonify({"notifications": _notifications})


#  Location check-ins 
@api_bp.route("/api/location-checkins", methods=["GET"])
def get_location_checkins():
    docs = list(
        location_checkins_collection.find().sort("created_at", -1).limit(200)
    )
    checkins = [{
        "employee_id": d.get("employee_id"),
        "employee_name": d.get("employee_name", ""),
        "scan_id": d.get("scan_id", ""),   # NEW: para sa deterministic matching sa dashboard.html
        "lat": d.get("lat"),
        "lng": d.get("lng"),
        "accuracy": d.get("accuracy"),
        "timestamp": d.get("timestamp"),
    } for d in docs]
    return jsonify({"checkins": checkins})


@api_bp.route("/api/userback", methods=["POST"])
def create_location_checkin():
    """Called by user_dashboard.html Step 3 (GPS check-in) to record an
    employee's location. This is the POST counterpart of the GET
    /api/location-checkins route above."""
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


# Stats
@api_bp.route("/api/stats", methods=["GET"])
def get_stats():
    total_employees = employees_collection.count_documents({})

    today = datetime.now().date()
    checked_in_ids = set()
    for doc in scans_collection.find({"scan_type": "time_in"}):
        verified_at = doc.get("verified_at")
        if verified_at and getattr(verified_at, "date", lambda: None)() == today:
            if _is_fully_verified(doc):
                checked_in_ids.add(doc.get("employee_id"))

    on_leave = leaves_collection.count_documents({"status": "Approved"})

    return jsonify({
        "totalEmployees": total_employees,
        "checkedIn": len(checked_in_ids),
        "onLeave": on_leave,
        "onDuty": len(checked_in_ids),
    })


#  Analytics 
@api_bp.route("/api/analytics", methods=["GET"])
def get_analytics():
    total_employees = max(1, employees_collection.count_documents({}))

    now = datetime.now()
    week_ago = now - timedelta(days=7)
    recent_scans = list(scans_collection.find({"verified_at": {"$gte": week_ago}}))

    total_scans = len(recent_scans)
    fully_verified_scans = sum(1 for s in recent_scans if _is_fully_verified(s))

    attendance_rate = round((fully_verified_scans / total_scans) * 100, 1) if total_scans else 0
    on_time_rate = attendance_rate  # placeholder pa rin
    approved_leaves = leaves_collection.count_documents({"status": "Approved"})
    leave_utilization = round((approved_leaves / total_employees) * 100, 1)
    avg_hours = 8  # placeholder pa rin

    day_counts = {}
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).strftime("%a")
        day_counts[day] = 0
    for s in recent_scans:
        verified_at = s.get("verified_at")
        if verified_at and _is_fully_verified(s):
            day_counts[verified_at.strftime("%a")] = day_counts.get(verified_at.strftime("%a"), 0) + 1

    weekly_trend = [{"day": d, "checkins": c} for d, c in day_counts.items()]

    return jsonify({
        "attendanceRate": {"value": attendance_rate, "formula": "fully verified (QR+Face+Location) scans ÷ total scans (last 7 days)"},
        "onTimeRate": {"value": on_time_rate, "formula": "placeholder — needs shift start time comparison"},
        "leaveUtilization": {"value": leave_utilization, "formula": "approved leaves ÷ total employees"},
        "avgHours": {"value": avg_hours, "formula": "placeholder — needs time_in/time_out pairing"},
        "weeklyTrend": weekly_trend,
    })


@api_bp.route("/api/log-attendance", methods=["GET"])
def latest_attendance():
    scans = fetch_recent_scans(50)

    data = []
    for scan in scans:
        data.append({
            "name": scan.get("name", ""),
            "employee_id": scan.get("employee_id", ""),
            "score": scan.get("score", 0),
            "status": "verified" if _is_fully_verified(scan) else "unverified",
            "verified_at": scan.get("verified_at", ""),
            "photo": scan.get("photo", "")
        })

    return jsonify(data)