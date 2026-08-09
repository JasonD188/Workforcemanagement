from flask import Blueprint, render_template, request, jsonify

from deepfacerecog_controller import DeepFaceRecogController, load_known_faces

deepfacerecog_bp = Blueprint("deepfacerecog_bp", __name__)


@deepfacerecog_bp.route("/")
def index():
    return render_template("uiface.html")


@deepfacerecog_bp.route("/register")
def register():
    return render_template("registerface.html")


@deepfacerecog_bp.route("/scan", methods=["POST"])
def scan():
    data = request.get_json()
    result = DeepFaceRecogController.scan_face(data)
    return jsonify(result)


@deepfacerecog_bp.route("/log-attendance", methods=["POST"])
def log_attendance():
    try:
        data = request.get_json()

        employee_id = data.get("employee_id")
        name = data.get("name")
        score = data.get("score", 0)
        scanned_b64 = data.get("scanned_image")
        registered_b64 = data.get("registered_image")
        scan_type = data.get("scan_type")  # "time_in" o "time_out ng user"
        qr_code_image = data.get("qr_code_image")

        if not employee_id or not name:
            return jsonify({
                "success": False,
                "error": "Missing employee_id or name."
            }), 400

        if scan_type not in ("time_in", "time_out"):
            return jsonify({
                "success": False,
                "error": "scan_type must be 'time_in' or 'time_out'."
            }), 400

        result = DeepFaceRecogController.log_attendance(
            employee_id, name, score, scanned_b64, registered_b64,
            scan_type, qr_code_image
        )

        return jsonify(result)

    except Exception as e:
        print("ERROR logging attendance:", e)
        return jsonify({
            "success": False,
            "error": "Server error while logging attendance."
        }), 500


@deepfacerecog_bp.route("/api/scan-logs")
def api_scan_logs():
 
    try:
        logs = DeepFaceRecogController.get_scan_logs()
        return jsonify({"logs": logs})

    except Exception as e:
        print("ERROR fetching scan logs:", e)
        return jsonify({"logs": [], "error": "Could not load scan logs."}), 500


@deepfacerecog_bp.route("/reload-faces", methods=["POST"])
def reload_faces():
  
    try:
        load_known_faces()
        return jsonify({"success": True, "message": "Faces reloaded."})
    except Exception as e:
        print("ERROR reloading faces:", e)
        return jsonify({"success": False, "error": str(e)}), 500