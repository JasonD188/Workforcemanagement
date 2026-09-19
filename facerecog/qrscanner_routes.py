from flask import Blueprint, render_template, jsonify, session

from qrscanner_controller import QRCodeScannerController

qrcodescanner_bp = Blueprint("qrcodescanner_bp", __name__)


@qrcodescanner_bp.route("/register-page")
def register_page():
    return render_template("registerface.html")


@qrcodescanner_bp.route("/")
def scanner():
    return render_template("qrcodescanner.html")


@qrcodescanner_bp.route("/employee/<employee_id>")
def employee(employee_id):
    # --- SECURITY FIX -------------------------------------------------
    # Ito ang backend enforcement ng "sarili lang na QR ang pwedeng
    # ma-scan sa account na naka-login". Ang dashboard.html (frontend)
    # na lang ay UX guard na madaling ma-bypass (e.g. via browser
    # console). Dito sa server, kinukuha ang employee_id ng NAKA-LOGIN
    # NA SESSION at ikinukumpara sa employee_id na na-scan.
    #
    # NOTE: Palitan ang "employee_id" sa ibaba ng TALAGANG session key
    # na ginagamit ng login route mo (allfiles.py / auth blueprint).
    # Kung iba ang key (hal. "emp_id", "user_id"), i-update dito.
    logged_in_id = session.get("employee_id")

    if logged_in_id and str(logged_in_id) != str(employee_id):
        return jsonify({
            "success": False,
            "message": "Hindi tugma ang na-scan na QR sa iyong naka-login na account."
        }), 403

    result = QRCodeScannerController.get_employee_details(employee_id)

    if result is None:
        return jsonify({
            "success": False,
            "message": "Employee not found"
        }), 404

    return jsonify(result)