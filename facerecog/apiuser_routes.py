# apiuser_routes.py
import traceback
from datetime import date, datetime

from flask import Blueprint, request, jsonify, session, current_app
from database.postgress import get_connection
from auth.utils import clean_str


apiuser_bp = Blueprint("apiuser_bp", __name__)


# ---------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------
# Ang employee_id ay dapat galing sa SESSION (login), hindi sa ipinasa ng
# browser — kung hindi, puwedeng mag-file/magbasa ng leave at notification
# ng ibang employee ang kahit sino sa pagpalit lang ng employee_id.
#
# I-set ang SESSION_EMPLOYEE_KEY sa key na ginagamit ng /api/me mo sa
# pag-save ng logged-in employee (hal. "employee_id").
#
# Habang hindi pa sigurado ang key, ALLOW_CLIENT_EMPLOYEE_ID = True ang
# fallback (gagamitin ang employee_id ng client KUNG WALANG laman ang
# session). Kapag na-verify na gumagana ang session, gawing False.
SESSION_EMPLOYEE_KEY = "employee_id"
ALLOW_CLIENT_EMPLOYEE_ID = True

# TEMPORARY: habang hinahanap ang 500 error. Kapag True, ang mismong
# error text ay ipapakita sa pulang message ng leave form (at nasa
# terminal din ang buong traceback). GAWING False pagkatapos ma-ayos.
DEBUG_ERRORS = True


def _fail(message, exc):
    current_app.logger.exception(message)   # buong traceback sa terminal
    traceback.print_exc()
    if DEBUG_ERRORS:
        return jsonify({"error": f"{message} [{type(exc).__name__}: {exc}]"}), 500
    return jsonify({"error": message}), 500


def resolve_employee_id(client_value=None):
    from_session = clean_str(session.get(SESSION_EMPLOYEE_KEY))
    if from_session:
        return from_session  # session ang panalo, kahit iba ang ipinasa ng client
    if ALLOW_CLIENT_EMPLOYEE_ID:
        return clean_str(client_value)
    return None


def _json_safe(row: dict) -> dict:
    """Gawing ISO string ang lahat ng date/datetime (start_date/end_date =
    'YYYY-MM-DD'), para hindi na kailangang umasa sa default ng Flask."""
    out = {}
    for k, v in row.items():
        out[k] = v.isoformat() if isinstance(v, date) else v  # datetime ⊂ date
    return out


LEAVE_COLS = """id, name, dates, qr_id, status, created_at, processed_at,
                leave_type, start_date, end_date, reason"""


# Ang `leaves` table ay may NOT NULL na `employee_id` column (hiwalay sa
# `qr_id`). Depende sa type nito ang ilalagay:
#   - text/varchar  -> ang badge/QR id (hal. "f9a53db4")
#   - integer/uuid  -> ang primary key (`id`) ng row sa `employees`
# Isang beses lang tinitingnan ang type at naka-cache.
_LEAVES_EMP_COL_IS_TEXT = None


def _leave_employee_fk(cur, badge_id, registered_pk):
    global _LEAVES_EMP_COL_IS_TEXT
    if _LEAVES_EMP_COL_IS_TEXT is None:
        cur.execute(
            """
            SELECT data_type FROM information_schema.columns
            WHERE table_name = 'leaves' AND column_name = 'employee_id'
            """
        )
        r = cur.fetchone()
        _LEAVES_EMP_COL_IS_TEXT = bool(r) and r[0] in (
            "character varying", "text", "character"
        )
    return badge_id if _LEAVES_EMP_COL_IS_TEXT else registered_pk


# ---------------------------------------------------------------------
# Location check-in
# ---------------------------------------------------------------------

@apiuser_bp.route("/api/checkin", methods=["POST"])
def create_location_checkin():
    """create ng location checkin record sa user"""
    data = request.get_json(silent=True) or {}

    employee_id = resolve_employee_id(data.get("employee_id"))
    scan_id = clean_str(data.get("scan_id"))
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy")
    timestamp = data.get("timestamp") or datetime.utcnow().isoformat() + "Z"

    if lat is None or lng is None:
        return jsonify({"error": "Missing location coordinates."}), 400

    conn = get_connection()
    employee_name = ""
    try:
        cur = conn.cursor()

        if employee_id:
            cur.execute(
                "SELECT name FROM employees WHERE employee_id = %s", (employee_id,)
            )
            row = cur.fetchone()
            if row:
                employee_name = row[0]

        # NOTE: utcnow() ang created_at dito dahil ito ang kinukumpara ng
        # _is_fully_verified() / analytics sa scan_logs.verified_at (UTC).
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

            # FIX: dati ay may try/except na nilulunok ang error dito. Sa
            # Postgres, kapag pumalya ang isang statement ang buong
            # transaction ay "aborted" — kaya ang conn.commit() ay tahimik
            # na nagro-rollback, nawawala pati ang INSERT sa itaas, pero
            # "Attendance recorded." pa rin ang sagot. Ngayon, hinahayaang
            # umabot ang error sa outer except (rollback + 500).
            cur.execute(
                "SELECT score, employee_id FROM scan_logs WHERE id = %s", (scan_id,)
            )
            scan_row = cur.fetchone()
            if scan_row:
                score = scan_row[0] or 0
                scan_owner = scan_row[1]
                # Huwag hayaang i-verify ng ibang employee ang scan ng iba.
                if employee_id and scan_owner and scan_owner != employee_id:
                    conn.rollback()
                    return jsonify({"error": "This scan does not belong to you."}), 403

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

        conn.commit()
        cur.close()
    except Exception:
        traceback.print_exc()
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


#  Leave Requests (user-facing) 
#
# WALANG @role_required dito dahil ito mismo ang endpoint na tatawagin ng
# user/employee app para mag-file ng sariling leave request. Ang parehong
# `leaves` table ang binabasa ng Admin Dashboard > Leave Management
# (ApiController.get_leaves()), kaya awtomatikong lalabas dito ang bawat
# leave request na isinumite ng user.
#
# Structured fields (leave_type, start_date, end_date, reason) ang
# sinasave bilang hiwalay na columns. Ang `dates` ay pinapanatili pa rin
# bilang legacy/fallback display string.

@apiuser_bp.route("/api/user/leaves", methods=["POST"])
def create_user_leave_request():
    """Isinusumite ng employee/user ang sarili niyang leave request.

    Ang `name` ay kinukuha sa DB base sa employee_id (hindi sa JSON body)
    para hindi mai-spoof. Laging "Pending" sa unang pagsumite; ang admin
    ang mag-a-approve/decline.
    """
    data = request.get_json(silent=True) or {}

    employee_id = resolve_employee_id(data.get("employee_id"))
    leave_type = (clean_str(data.get("leave_type")) or "Leave")[:50]
    start_raw = clean_str(data.get("start_date"))
    end_raw = clean_str(data.get("end_date")) or start_raw
    reason = (clean_str(data.get("reason")) or "")[:1000]

    if not employee_id:
        return jsonify({"error": "Not signed in."}), 401
    if not start_raw:
        return jsonify({"error": "Missing start date."}), 400

    try:
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
    except ValueError:
        return jsonify({"error": "Invalid date format."}), 400
    if end_date < start_date:
        return jsonify({"error": "End date cannot be earlier than start date."}), 400

    # Legacy display string — para sa lumang code/UI na umaasa pa sa `dates`.
    range_label = (
        start_raw if start_date == end_date else f"{start_raw} to {end_raw}"
    )
    dates = f"{range_label} ({leave_type})"

    conn = get_connection()
    try:
        cur = conn.cursor()

        # Hanapin muna sa registered employees, tapos sa quick-add.
        registered_pk = None
        cur.execute(
            "SELECT name, id FROM employees WHERE employee_id = %s AND deleted_at IS NULL",
            (employee_id,)
        )
        row = cur.fetchone()
        if row:
            registered_pk = row[1]

        if not row:
            cur.execute(
                "SELECT name FROM quick_add_employees WHERE employee_id = %s AND deleted_at IS NULL",
                (employee_id,)
            )
            row = cur.fetchone()

        if not row:
            return jsonify({"error": "Employee not found."}), 404

        employee_name = row[0] or ""

        leave_employee_fk = _leave_employee_fk(cur, employee_id, registered_pk)
        if leave_employee_fk is None:
            return jsonify({
                "error": "Only registered employees can file a leave request."
            }), 400

        # Iwas duplicate (double-click / ulit pagkatapos ng error).
        cur.execute(
            """
            SELECT 1 FROM leaves
            WHERE qr_id = %s AND leave_type = %s
              AND start_date = %s AND end_date = %s AND status = 'Pending'
            LIMIT 1
            """,
            (employee_id, leave_type, start_date, end_date)
        )
        if cur.fetchone():
            return jsonify({"error": "You already filed this leave request."}), 409

        cur.execute(
            f"""
            INSERT INTO leaves (employee_id, name, dates, qr_id, status, created_at,
                                 leave_type, start_date, end_date, reason)
            VALUES (%s, %s, %s, %s, 'Pending', NOW(), %s, %s, %s, %s)
            RETURNING {LEAVE_COLS}
            """,
            (leave_employee_fk, employee_name, dates, employee_id,
             leave_type, start_date, end_date, reason)
        )
        columns = [desc[0] for desc in cur.description]
        leave = _json_safe(dict(zip(columns, cur.fetchone())))
        conn.commit()
        cur.close()
    except Exception as e:
        conn.rollback()
        return _fail("Failed to submit leave request.", e)
    finally:
        conn.close()

    try:
        return jsonify({"success": True, "leave": leave}), 201
    except Exception as e:                       # JSON/serialization problem
        return _fail("Leave saved but response failed.", e)


@apiuser_bp.route("/api/user/leaves", methods=["GET"])
def get_user_leave_requests():
    """Ang sariling leave history/status (Pending/Approved/Declined) ng user."""
    employee_id = resolve_employee_id(request.args.get("employee_id"))
    if not employee_id:
        return jsonify({"error": "Not signed in."}), 401

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT {LEAVE_COLS}
            FROM leaves
            WHERE qr_id = %s
            ORDER BY id DESC
            """,
            (employee_id,)
        )
        columns = [desc[0] for desc in cur.description]
        leaves = [_json_safe(dict(zip(columns, r))) for r in cur.fetchall()]
        cur.close()
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Failed to load leave requests."}), 500
    finally:
        conn.close()

    return jsonify({"leaves": leaves})


#  Notifications (user-facing)
#
# Per-employee ang notifications — galing sa `user_notifications` table
# (tingnan ang 1_user_notifications.sql). Ginagawa ang row nito sa
# ApiController/_set_leave_status() sa tuwing ina-approve o dine-decline
# ng admin ang leave request, sa parehong transaction ng status update.
#
# Katulad ng /api/user/leaves, walang @role_required dito dahil para ito
# sa employee, hindi sa admin. (Ang /api/notifications sa api_routes.py ay
# admin-only at ibang bagay — huwag itong gamitin sa user dashboard.)

@apiuser_bp.route("/api/user/notifications", methods=["GET"])
def get_user_notifications():
    """Notifications ng isang employee (pinakabago muna) + unread count."""
    employee_id = resolve_employee_id(request.args.get("employee_id"))
    if not employee_id:
        return jsonify({"error": "Not signed in."}), 401

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, title, body, type, ref_id, is_read, created_at
            FROM user_notifications
            WHERE employee_id = %s
            ORDER BY id DESC
            LIMIT 50
            """,
            (employee_id,)
        )
        columns = [desc[0] for desc in cur.description]
        items = [_json_safe(dict(zip(columns, r))) for r in cur.fetchall()]

        # Hiwalay na COUNT para tama ang unread kahit lampas 50 ang notifications.
        cur.execute(
            "SELECT COUNT(*) FROM user_notifications WHERE employee_id = %s AND is_read = FALSE",
            (employee_id,)
        )
        unread = cur.fetchone()[0]
        cur.close()
    except Exception:
        traceback.print_exc()
        return jsonify({"error": "Failed to load notifications."}), 500
    finally:
        conn.close()

    return jsonify({"notifications": items, "unread": unread})


@apiuser_bp.route("/api/user/notifications/mark-all-read", methods=["POST"])
def mark_user_notifications_read():
    """I-mark na read ang lahat ng notifications ng employee."""
    data = request.get_json(silent=True) or {}
    employee_id = resolve_employee_id(data.get("employee_id"))
    if not employee_id:
        return jsonify({"error": "Not signed in."}), 401

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE user_notifications SET is_read = TRUE WHERE employee_id = %s AND is_read = FALSE",
            (employee_id,)
        )
        conn.commit()
        cur.close()
    except Exception:
        traceback.print_exc()
        conn.rollback()
        return jsonify({"error": "Failed to update notifications."}), 500
    finally:
        conn.close()

    return jsonify({"success": True})