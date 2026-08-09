from flask import Blueprint, render_template, jsonify

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
    result = QRCodeScannerController.get_employee_details(employee_id)

    if result is None:
        return jsonify({
            "success": False,
            "message": "Employee not found"
        }), 404

    return jsonify(result)