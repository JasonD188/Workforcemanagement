from datetime import datetime, timedelta, timezone
import base64

from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id
from api import fetch_recent_scans
from api_analytics.analytics_export import AnalyticsExportController
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
    """Ginagawang dict ang isang DB row batay sa PANGALAN ng column (hindi
    sa posisyon). Ito ang dapat laging gamitin sa halip na direktang
    tuple-unpacking (hal. `for a, b, c in cur.fetchall()`), dahil ang
    positional unpacking ay masisira kapag nagbago ang pagkakasunod-sunod
    ng column sa query o sa table — kahit walang logic na binago."""
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
               """
                SELECT name, employee_id, contact, address, date_hired, photo_file_id, created_at
                FROM employees
                WHERE deleted_at IS NULL
                ORDER BY name ASC
                """
            )
            # FIX: dati ay direktang positional tuple-unpack
            # (`for name, employee_id, ... in cur.fetchall():`) — kaya kapag
            # nagbago ang order ng columns sa SELECT o sa table mismo, mali
            # na ang mapunta sa bawat variable nang walang nakikitang error.
            # Ngayon, dict-based na ito gamit ang _row_to_dict(), kaya base
            # sa PANGALAN ng column kinukuha ang value — hindi maaapektuhan
            # ng pagbabago sa order.
            for row in cur.fetchall():
                emp = _row_to_dict(cur, row)
                name = emp.get("name")
                employee_id = emp.get("employee_id")
                if not name or not employee_id:
                    continue
                created_at = emp.get("created_at")
                date_hired = emp.get("date_hired")
                roster.append({
                    "name": name,
                    "employeeId": employee_id,
                    "contact": emp.get("contact") or "",
                    "address": emp.get("address") or "",
                    "dateHired": _format_date_hired(created_at) if created_at else (
                        date_hired.isoformat() if date_hired else ""
                    ),
                    "photo": _get_employee_photo_data_url(emp.get("photo_file_id")),
                    "source": "registered",
                })
                seen_names.add(name)

            cur.execute(
                "SELECT name, employee_id FROM quick_add_employees WHERE deleted_at IS NULL"
            )
            for row in cur.fetchall():
                qa = _row_to_dict(cur, row)
                name = qa.get("name")
                employee_id = qa.get("employee_id")
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
        """Soft-delete: manatili ang row sa employees (para sa FK integrity),
        pero i-mark ang deleted_at at i-log din sa deleted_employees bilang archive."""
        conn = get_connection()
        try:
            cur = conn.cursor()

            cur.execute(
                """
                UPDATE employees
                SET deleted_at = %s, deleted_by = %s
                WHERE employee_id = %s AND deleted_at IS NULL
                RETURNING employee_id, name, contact, address, date_hired, qr_path, qr_code_value,
                          photo_file_id, photo, photo_data, face_registered, created_at
                """,
                (datetime.utcnow(), deleted_by, employee_id)
            )
            row = cur.fetchone()

            if row:
                # FIX: dati ay malaking positional tuple-unpack:
                #   (emp_id, name, contact, address, date_hired, qr_path,
                #    qr_code_value, photo_file_id, photo, photo_data,
                #    face_registered, created_at) = row
                # Napaka-delikado nito — kahit isang column lang ang
                # ma-reorder o madagdag sa RETURNING/table, mali na agad
                # lahat ng susunod na value. Ngayon, dict-based na
                # (_row_to_dict) at ang INSERT sa ibaba ay gumagamit na ng
                # NAMED parameters (%(column)s) sa halip na positional
                # (%s, %s, %s...), kaya base sa pangalan ng column
                # ipinapasok ang bawat value, hindi sa pagkakasunod-sunod.
                emp = _row_to_dict(cur, row)

                cur.execute(
                    """
                    INSERT INTO deleted_employees
                        (employee_id, name, contact, address, date_hired, qr_path, qr_code_value,
                         photo_file_id, photo, photo_data, face_registered, created_at,
                         deleted_at, deleted_by)
                    VALUES (%(employee_id)s, %(name)s, %(contact)s, %(address)s, %(date_hired)s,
                            %(qr_path)s, %(qr_code_value)s, %(photo_file_id)s, %(photo)s,
                            %(photo_data)s, %(face_registered)s, %(created_at)s,
                            %(deleted_at)s, %(deleted_by)s)
                    ON CONFLICT (employee_id) DO UPDATE SET
                        deleted_at = EXCLUDED.deleted_at,
                        deleted_by = EXCLUDED.deleted_by
                    """,
                    {
                        **emp,
                        "deleted_at": datetime.utcnow(),
                        "deleted_by": deleted_by,
                    }
                )
                conn.commit()
            else:
                cur.execute(
                    """
                    UPDATE quick_add_employees
                    SET deleted_at = %s, deleted_by = %s
                    WHERE employee_id = %s AND deleted_at IS NULL
                    RETURNING employee_id
                    """,
                    (datetime.utcnow(), deleted_by, employee_id)
                )
                row = cur.fetchone()
                conn.commit()

                if not row:
                    return {"error": "Employee not found."}, 404
        finally:
            conn.close()

        return cls.get_employees(), 200

    @classmethod
    def restore_employee(cls, employee_id):
        """I-clear ang deleted_at sa employees (row nandoon pa rin talaga),
        at alisin sa deleted_employees log."""
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
            row = cur.fetchone()

            if row:
                cur.execute("DELETE FROM deleted_employees WHERE employee_id = %s", (employee_id,))
                conn.commit()
            else:
                cur.execute(
                    """
                    UPDATE quick_add_employees
                    SET deleted_at = NULL, deleted_by = NULL
                    WHERE employee_id = %s AND deleted_at IS NOT NULL
                    RETURNING employee_id
                    """,
                    (employee_id,)
                )
                row = cur.fetchone()
                conn.commit()

                if not row:
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
                """
                SELECT name, employee_id, deleted_at, deleted_by
                FROM deleted_employees
                ORDER BY deleted_at DESC
                """
            )
            # FIX: dati ay `for name, employee_id, deleted_at, deleted_by in
            # cur.fetchall():` — pareho ring positional. Ginawang dict-based.
            for row in cur.fetchall():
                d = _row_to_dict(cur, row)
                deleted_at = d.get("deleted_at")
                deleted.append({
                    "name": d.get("name"),
                    "employee_id": d.get("employee_id"),
                    "deleted_at": deleted_at.isoformat() if deleted_at else None,
                    "deleted_by": d.get("deleted_by") or "",
                    "source": "registered",
                })

            cur.execute(
                """
                SELECT name, employee_id, deleted_at, deleted_by
                FROM quick_add_employees
                WHERE deleted_at IS NOT NULL
                ORDER BY deleted_at DESC
                """
            )
            for row in cur.fetchall():
                d = _row_to_dict(cur, row)
                deleted_at = d.get("deleted_at")
                deleted.append({
                    "name": d.get("name"),
                    "employee_id": d.get("employee_id"),
                    "deleted_at": deleted_at.isoformat() if deleted_at else None,
                    "deleted_by": d.get("deleted_by") or "",
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
                """
                SELECT id, shift_name, date, time, time_in, time_out, location, type,
                       employees, employee_qr_id, replacing_employee, replacing_employee_qr_id,
                       lat, lng, created_at
                FROM schedules
                ORDER BY id ASC
                """
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
                """
                SELECT id, shift_name, date, time, time_in, time_out, location, type,
                       employees, employee_qr_id, replacing_employee, replacing_employee_qr_id,
                       lat, lng, created_at
                FROM schedules
                ORDER BY id ASC
                """
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
                """
                SELECT id, shift_name, date, time, time_in, time_out, location, type,
                       employees, employee_qr_id, replacing_employee, replacing_employee_qr_id,
                       lat, lng, created_at
                FROM schedules
                ORDER BY id ASC
                """
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
            """
            SELECT *, created_at AS timestamp FROM location_checkins
            """
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

        return {"attendance": data}

    #  Analytics 

    @staticmethod
    def get_analytics():
        """
        Kinukwenta ang mga workforce analytics metrics (Attendance Rate,
        On-Time Rate, Leave Utilization, Avg. Hours Logged) at ang
        weekly scan trend - lahat batay sa FULLY VERIFIED na scan_logs
        (parehong batayan ng Monitoring/_has_location_checkin at ng
        Analytics export/_fetch_scan_logs), kaya magkakatugma ang mga
        numero sa buong dashboard.
        """
        today = datetime.utcnow().date()
        range_start = today - timedelta(days=29)  # 30-day window para sa rates
        trend_start = today - timedelta(days=6)    # 7-day window para sa graph

        attendance_df = AnalyticsExportController.build_attendance_record_df(range_start, today)
        time_df = AnalyticsExportController.build_time_in_out_df(range_start, today)
        leave_df = AnalyticsExportController.build_leave_df()

        day_columns = [c for c in attendance_df.columns if c not in ("employee_id", "name")]
        total_cells = len(attendance_df) * len(day_columns) if day_columns else 0

        present_count = 0
        late_count = 0
        if total_cells:
            for col in day_columns:
                counts = attendance_df[col].value_counts()
                present_count += int(counts.get("Present", 0))
                late_count += int(counts.get("Late", 0))

        worked_count = present_count + late_count
        attendance_rate = round((worked_count / total_cells) * 100, 1) if total_cells else 0.0
        on_time_rate = round((present_count / worked_count) * 100, 1) if worked_count else 0.0

        total_employees = len(AnalyticsExportController._fetch_employees())
        leave_utilization = round((len(leave_df) / total_employees) * 100, 1) if total_employees else 0.0

        avg_hours = 0.0
        if not time_df.empty:
            complete = time_df.dropna(subset=["time_in", "time_out"])
            if not complete.empty:
                hours = (complete["time_out"] - complete["time_in"]).dt.total_seconds() / 3600
                avg_hours = round(hours.mean(), 1)

        conn = get_connection()
        weekly_trend = []
        try:
            cur = conn.cursor()
            for i in range(7):
                day = trend_start + timedelta(days=i)
                cur.execute(
                    """
                    SELECT COUNT(*) FROM scan_logs sl
                    WHERE sl.verified_at::date = %s
                      AND EXISTS (
                          SELECT 1 FROM location_checkins lc
                          WHERE lc.scan_id::text = sl.id::text
                             OR (
                                 lc.employee_id = sl.employee_id
                                 AND lc.created_at BETWEEN sl.verified_at
                                     AND sl.verified_at + INTERVAL '15 minutes'
                             )
                      )
                    """,
                    (day,)
                )
                count = cur.fetchone()[0]
                weekly_trend.append({"day": day.strftime("%a"), "checkins": count})
        finally:
            conn.close()

        return {
            "attendanceRate": {
                "value": attendance_rate,
                "formula": f"(Present + Late days) / (Employees x Workdays) x 100, last 30 days = ({worked_count} / {total_cells}) x 100"
            },
            "onTimeRate": {
                "value": on_time_rate,
                "formula": f"Present days / (Present + Late days) x 100, last 30 days = ({present_count} / {worked_count}) x 100"
            },
            "leaveUtilization": {
                "value": leave_utilization,
                "formula": f"Approved leave requests / Total employees x 100 = ({len(leave_df)} / {total_employees}) x 100"
            },
            "avgHours": {
                "value": avg_hours,
                "formula": "Average of (Time Out minus Time In) across completed time-in/time-out pairs, last 30 days"
            },
            "weeklyTrend": weekly_trend,
        }