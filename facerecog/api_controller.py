from datetime import datetime, timedelta, timezone
import base64

from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id
from api import fetch_recent_scans

LOCATION_MATCH_WINDOW_MINUTES = 15

_notifications = [
    {"id": 1, "title": "Welcome", "body": "Dashboard connected.", "time": "just now", "read": False},
]


def _quick_add_qr_id(name):
    slug = "".join(ch for ch in name.upper() if ch.isalnum())[:6] or "EMP"
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return f"QUICK-{slug}-{(h % 9000) + 1000}"


def _is_fully_verified(scan):
  
    from deepfacerecog_controller import MIN_ACCESS_SCORE_PERCENT

    if (scan.get("score") or 0) < MIN_ACCESS_SCORE_PERCENT:
        return False

    scan_id = scan.get("id")
    conn = get_connection()
    try:
        cur = conn.cursor()

        if scan_id:
            cur.execute(
                "SELECT 1 FROM location_checkins WHERE scan_id = %s LIMIT 1",
                (str(scan_id),)
            )
            if cur.fetchone():
                return True

        verified_at = scan.get("verified_at")
        if not verified_at:
            return False

        verified_at_utc = (
            verified_at if verified_at.tzinfo is None
            else verified_at.astimezone(timezone.utc).replace(tzinfo=None)
        )
        window_end = verified_at_utc + timedelta(minutes=LOCATION_MATCH_WINDOW_MINUTES)

        cur.execute(
            """
            SELECT 1 FROM location_checkins
            WHERE employee_id = %s AND created_at BETWEEN %s AND %s
            LIMIT 1
            """,
            (scan.get("employee_id"), verified_at_utc, window_end)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


def _get_employee_photo_data_url(photo_file_id):
  
    if not photo_file_id:
        return ""
    try:
        jpeg_bytes = storage.get_file_view(bucket_id=bucket_id, file_id=photo_file_id)
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        print(f"Could not load photo from Appwrite Storage ({photo_file_id}):", e)
        return ""


PH_TZ = timezone(timedelta(hours=8)) 


def _format_date_hired(date_hired):
   
    if not date_hired:
        return ""

    if date_hired.tzinfo is None:
       
        date_hired = date_hired.replace(tzinfo=timezone.utc)

    local_dt = date_hired.astimezone(PH_TZ)

    try:
        return local_dt.strftime("%Y-%m-%d %#I:%M %p")  # Windows
    except ValueError:
        return local_dt.strftime("%Y-%m-%d %-I:%M %p")  # Linux/Mac


def _row_to_dict(cur, row):
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))


def _set_leave_status(leave_id, status):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE leaves
            SET status = %s, processed_at = %s
            WHERE id = %s
            RETURNING id, name, dates, qr_id, status, created_at, processed_at
            """,
            (status, datetime.now().isoformat(), leave_id)
        )
        row = cur.fetchone()
        conn.commit()
        if not row:
            return None
        return _row_to_dict(cur, row)
    finally:
        conn.close()


class ApiController:
   

    #  Employees 

    @staticmethod
    def get_employees():
        roster = []
        seen_names = set()

        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
               
            )
            for name, employee_id, contact, address, date_hired, photo_file_id, created_at in cur.fetchall():
                if not name or not employee_id:
                    continue
                roster.append({
                    "name": name,
                    "employeeId": employee_id,
                    "contact": contact or "",
                    "address": address or "",                 
                    "dateHired": _format_date_hired(created_at) if created_at else (
                        date_hired.isoformat() if date_hired else ""
                    ),
                    "photo": _get_employee_photo_data_url(photo_file_id),
                    "source": "registered",
                })
                seen_names.add(name)

            cur.execute(
                "SELECT name, employee_id FROM quick_add_employees WHERE deleted_at IS NULL"
            )
            for name, employee_id in cur.fetchall():
                if not name or name in seen_names:
                    continue
                roster.append({
                    "name": name,
                    "employeeId": employee_id or _quick_add_qr_id(name),
                    "contact": "",
                    "address": "",
                    "dateHired": "",
                    "photo": "",
                    "source": "quick_add",
                })
                seen_names.add(name)
        finally:
            conn.close()

        roster.sort(key=lambda e: e["name"].lower())
        names = [e["name"] for e in roster]
        return {"employees": names, "roster": roster}

    @classmethod
    def add_employee(cls, name):
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
                "SELECT 1 FROM employees WHERE name ILIKE %s LIMIT 1", (name,)
            )
            if cur.fetchone():
                return {"error": f"{name} is already registered via Register Employee."}, 400

            cur.execute(
                "SELECT 1 FROM quick_add_employees WHERE name ILIKE %s LIMIT 1", (name,)
            )
            if not cur.fetchone():
                cur.execute(
                    """
                    INSERT INTO quick_add_employees (name, employee_id, created_at)
                    VALUES (%s, %s, %s)
                    """,
                    (name, _quick_add_qr_id(name), datetime.utcnow())
                )
                conn.commit()
        finally:
            conn.close()

        return cls.get_employees(), 200

    @classmethod
    def remove_employee_photo(cls, employee_id):
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
                "SELECT photo_file_id FROM employees WHERE employee_id = %s",
                (employee_id,)
            )
            row = cur.fetchone()
            if not row:
                return {"error": "Employee not found."}, 404

            photo_file_id = row[0]

            if photo_file_id:
                try:
                    storage.delete_file(bucket_id=bucket_id, file_id=photo_file_id)
                except Exception as delete_err:
                    print(f"Could not delete photo from Appwrite Storage ({photo_file_id}):", delete_err)

            cur.execute(
                "UPDATE employees SET photo_file_id = NULL WHERE employee_id = %s",
                (employee_id,)
            )
            conn.commit()
        finally:
            conn.close()

        return cls.get_employees(), 200

    @classmethod
    def soft_delete_employee(cls, employee_id, deleted_by=None):
        
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE employees
                SET deleted_at = %s, deleted_by = %s
                WHERE employee_id = %s AND deleted_at IS NULL
                RETURNING employee_id
                """,
                (datetime.utcnow(), deleted_by, employee_id)
            )
            updated = cur.fetchone()

            if not updated:
               
                cur.execute(
                    """
                    UPDATE quick_add_employees
                    SET deleted_at = %s, deleted_by = %s
                    WHERE employee_id = %s AND deleted_at IS NULL
                    RETURNING employee_id
                    """,
                    (datetime.utcnow(), deleted_by, employee_id)
                )
                updated = cur.fetchone()

            conn.commit()

            if not updated:
                return {"error": "Employee not found."}, 404
        finally:
            conn.close()

        return cls.get_employees(), 200

    @classmethod
    def restore_employee(cls, employee_id):
        """Ibalik ang isang na-soft-delete na employee (deleted_at = NULL)."""
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE employees
                SET deleted_at = NULL, deleted_by = NULL
                WHERE employee_id = %s AND deleted_at IS NOT NULL
                RETURNING employee_id
                """,
                (employee_id,)
            )
            updated = cur.fetchone()

            if not updated:
                cur.execute(
                    """
                    UPDATE quick_add_employees
                    SET deleted_at = NULL, deleted_by = NULL
                    WHERE employee_id = %s AND deleted_at IS NOT NULL
                    RETURNING employee_id
                    """,
                    (employee_id,)
                )
                updated = cur.fetchone()

            conn.commit()

            if not updated:
                return {"error": "Deleted employee not found."}, 404
        finally:
            conn.close()

        return cls.get_employees(), 200

    @staticmethod
    def get_deleted_employees():
     
        deleted = []
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
               
            )
            for name, employee_id, deleted_at, deleted_by in cur.fetchall():
                deleted.append({
                    "name": name,
                    "employee_id": employee_id,
                    "deleted_at": deleted_at.isoformat() if deleted_at else None,
                    "deleted_by": deleted_by or "",
                    "source": "registered",
                })

            cur.execute(
              
            )
            for name, employee_id, deleted_at, deleted_by in cur.fetchall():
                deleted.append({
                    "name": name,
                    "employee_id": employee_id,
                    "deleted_at": deleted_at.isoformat() if deleted_at else None,
                    "deleted_by": deleted_by or "",
                    "source": "quick_add",
                })
        finally:
            conn.close()

        deleted.sort(key=lambda e: e["deleted_at"] or "", reverse=True)
        return {"deleted": deleted}

    #  Schedules 

    @staticmethod
    def get_schedules():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
              
            )
            docs = [_row_to_dict(cur, row) for row in cur.fetchall()]
        finally:
            conn.close()
        return {"schedules": docs}

    @staticmethod
    def create_schedule(data):
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
            return {"error": "Missing shift details."}, 400

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO schedules
                    (shift_name, date, time, time_in, time_out, location, type,
                     employees, employee_qr_id, replacing_employee, replacing_employee_qr_id,
                     lat, lng, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, shift_name, date, time, time_in, time_out, location, type,
                          employees, employee_qr_id, replacing_employee, replacing_employee_qr_id,
                          lat, lng, created_at
                """,
                (shift_name, date, time, time_in, time_out, location, shift_type,
                 [employee], employee_qr_id, replacing_employee, replacing_employee_qr_id,
                 lat, lng, datetime.utcnow())
            )
            shift = _row_to_dict(cur, cur.fetchone())
            conn.commit()

            cur.execute(
               
            )
            docs = [_row_to_dict(cur, row) for row in cur.fetchall()]
        finally:
            conn.close()

        return {"schedules": docs, "shift": shift}, 200

    @staticmethod
    def assign_schedule(schedule_id, employee):
        if not employee:
            return {"error": "Employee is required."}, 400

        conn = get_connection()
        try:
            cur = conn.cursor()
            
            cur.execute(
                """
                UPDATE schedules
                SET employees = (
                    SELECT array_agg(DISTINCT e) FROM unnest(employees || %s::text[]) AS e
                )
                WHERE id = %s
                RETURNING id, shift_name, date, time, time_in, time_out, location, type,
                          employees, employee_qr_id, replacing_employee, replacing_employee_qr_id,
                          lat, lng, created_at
                """,
                ([employee], schedule_id)
            )
            row = cur.fetchone()
            if not row:
                return {"error": "Shift not found."}, 404
            shift = _row_to_dict(cur, row)
            conn.commit()

            cur.execute(
            
               
            )
            docs = [_row_to_dict(cur, r) for r in cur.fetchall()]
        finally:
            conn.close()

        return {"schedules": docs, "shift": shift}, 200

    #  Leaves 

    @staticmethod
    def get_leaves():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, dates, qr_id, status, created_at, processed_at FROM leaves ORDER BY id ASC"
            )
            docs = [_row_to_dict(cur, row) for row in cur.fetchall()]
        finally:
            conn.close()
        return {"leaves": docs}

    @staticmethod
    def create_leave(data):
        name = (data.get("name") or "").strip()
        dates = (data.get("dates") or "").strip()
        qr_id = (data.get("qrId") or "").strip()
        if not (name and dates):
            return {"error": "Name and dates are required."}, 400

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO leaves (name, dates, qr_id, status, created_at)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, name, dates, qr_id, status, created_at, processed_at
                """,
                (name, dates, qr_id, "Pending", datetime.now().isoformat())
            )
            leave = _row_to_dict(cur, cur.fetchone())
            conn.commit()
        finally:
            conn.close()

        return {"leave": leave}, 200

    @staticmethod
    def approve_leave(leave_id):
        leave = _set_leave_status(leave_id, "Approved")
        if not leave:
            return {"error": "Leave request not found."}, 404
        return {"leave": leave}, 200

    @staticmethod
    def decline_leave(leave_id):
        leave = _set_leave_status(leave_id, "Declined")
        if not leave:
            return {"error": "Leave request not found."}, 404
        return {"leave": leave}, 200

    #  Notifications 

    @staticmethod
    def get_notifications():
        return {"notifications": _notifications}

    @staticmethod
    def mark_all_read():
        for n in _notifications:
            n["read"] = True
        return {"notifications": _notifications}

    #  Location check-ins 

    @staticmethod
    def get_location_checkins():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
               
            )
            checkins = [_row_to_dict(cur, row) for row in cur.fetchall()]

            for c in checkins:
                meters = c.get("accuracy")
                if meters is not None:
                    percent = max(0, 100 - (meters / 100) * 100)
                    c["accuracy"] = round(percent, 1)
        finally:
            conn.close()
        return {"checkins": checkins}

    #  Stats 

    @staticmethod
    def get_stats():
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM employees")
            total_employees = cur.fetchone()[0]

            today = datetime.now().date()
            cur.execute(
                "SELECT id, employee_id, score, verified_at FROM scan_logs WHERE scan_type = %s",
                ("time_in",)
            )
            checked_in_ids = set()
            for row in cur.fetchall():
                scan = _row_to_dict(cur, row)
                verified_at = scan.get("verified_at")
                if verified_at and getattr(verified_at, "date", lambda: None)() == today:
                    if _is_fully_verified(scan):
                        checked_in_ids.add(scan.get("employee_id"))

            cur.execute("SELECT COUNT(*) FROM leaves WHERE status = %s", ("Approved",))
            on_leave = cur.fetchone()[0]
        finally:
            conn.close()

        return {
            "totalEmployees": total_employees,
            "checkedIn": len(checked_in_ids),
            "onLeave": on_leave,
            "onDuty": len(checked_in_ids),
        }

    #  Analytics 

    @staticmethod
    def get_analytics():
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute("SELECT COUNT(*) FROM employees")
            total_employees = max(1, cur.fetchone()[0])

            now = datetime.now()
            week_ago = now - timedelta(days=7)

            cur.execute(
                """
                SELECT id, employee_id, score, verified_at FROM scan_logs
                WHERE verified_at >= %s
                """,
                (week_ago,)
            )
            recent_scans = [_row_to_dict(cur, row) for row in cur.fetchall()]

            total_scans = len(recent_scans)
            fully_verified_scans = sum(1 for s in recent_scans if _is_fully_verified(s))

            attendance_rate = round((fully_verified_scans / total_scans) * 100, 1) if total_scans else 0
            on_time_rate = attendance_rate
            cur.execute("SELECT COUNT(*) FROM leaves WHERE status = %s", ("Approved",))
            approved_leaves = cur.fetchone()[0]
            leave_utilization = round((approved_leaves / total_employees) * 100, 1)
            avg_hours = 8

            day_counts = {}
            for i in range(6, -1, -1):
                day = (now - timedelta(days=i)).strftime("%a")
                day_counts[day] = 0
            for s in recent_scans:
                verified_at = s.get("verified_at")
                if verified_at and _is_fully_verified(s):
                    d = verified_at.strftime("%a")
                    day_counts[d] = day_counts.get(d, 0) + 1

            weekly_trend = [{"day": d, "checkins": c} for d, c in day_counts.items()]
        finally:
            conn.close()

        return {
            "attendanceRate": {"value": attendance_rate, "formula": "fully verified (QR+Face+Location) scans ÷ total scans (last 7 days)"},
            "onTimeRate": {"value": on_time_rate, "formula": "placeholder — needs shift start time comparison"},
            "leaveUtilization": {"value": leave_utilization, "formula": "approved leaves ÷ total employees"},
            "avgHours": {"value": avg_hours, "formula": "placeholder — needs time_in/time_out pairing"},
            "weeklyTrend": weekly_trend,
        }

    @staticmethod
    def latest_attendance():
        scans = fetch_recent_scans(50)

        data = []
        for scan in scans:
            if scan.get("status") == "pending_location":
                continue

            data.append({
                "name": scan.get("name", ""),
                "employee_id": scan.get("employee_id", ""),
                "score": scan.get("score", 0),
                "status": "verified" if _is_fully_verified(scan) else "unverified",
                "verified_at": scan.get("verified_at", ""),
                "photo": scan.get("photo", "")
            })

        return data