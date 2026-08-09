# apiuser_routes.py
from datetime import datetime

from flask import Blueprint, request, jsonify
from database.postgress import get_connection
from auth.utils import clean_str


apiuser_bp = Blueprint("apiuser_bp", __name__)


@apiuser_bp.route("/api/checkin", methods=["POST"])
def create_location_checkin():
    """create ng location checkin record sa user"""
    data = request.get_json(silent=True) or {}

    employee_id = clean_str(data.get("employee_id"))
    scan_id = clean_str(data.get("scan_id"))
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy")
    timestamp = data.get("timestamp") or datetime.utcnow().isoformat() + "Z"

    if lat is None or lng is None:
        return jsonify({"error": "Missing location coordinates."}), 400

    conn = get_connection()
    try:
        cur = conn.cursor()

        
        employee_name = ""
        if employee_id:
            cur.execute(
                "SELECT name FROM employees WHERE employee_id = %s", (employee_id,)
            )
            row = cur.fetchone()
            if row:
                employee_name = row[0]

        cur.execute(
            """
            INSERT INTO location_checkins
                (employee_id, employee_name, scan_id, lat, lng, accuracy, checkin_timestamp, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (employee_id, employee_name, scan_id, lat, lng, accuracy,
             timestamp, datetime.utcnow())
        )

      
        if scan_id:
            from deepfacerecog_controller import MIN_ACCESS_SCORE_PERCENT
            try:
                cur.execute(
                    "SELECT score FROM scan_logs WHERE id = %s", (scan_id,)
                )
                scan_row = cur.fetchone()
                if scan_row:
                    score = scan_row[0] or 0
                    final_status = (
                        "verified" if score >= MIN_ACCESS_SCORE_PERCENT else "unverified"
                    )
                    cur.execute(
                        """
                        UPDATE scan_logs
                        SET status = %s, location_lat = %s, location_lng = %s,
                            location_accuracy = %s, note = NULL
                        WHERE id = %s
                        """,
                        (final_status, lat, lng, accuracy, scan_id)
                    )
            except Exception as e:
                print("Could not update scan status after location check-in:", e)

        conn.commit()
        cur.close()
    except Exception as e:
        print("Could not save location check-in to PostgreSQL:", e)
        conn.rollback()
        return jsonify({"error": "Failed to save check-in."}), 500
    finally:
        conn.close()

    return jsonify(
        {
            "success": True,
            "employeeName": employee_name,
            "message": "Attendance recorded.",
            "lat": lat,
            "lng": lng,
        }
    )