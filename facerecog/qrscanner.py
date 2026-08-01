from flask import Blueprint, render_template, request, jsonify
import os
import base64
import qrcode
import uuid
from io import BytesIO

from api import client, db, employees_collection, fs

qrcodescanner_bp = Blueprint("qrcodescanner_bp", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_DIR = os.path.join(BASE_DIR, "qrcodes")

os.makedirs(QR_DIR, exist_ok=True)



def get_employee(employee_id):
    """Fetch an employee record from MongoDB (no local JSON file anymore)."""
    return employees_collection.find_one(
        {"employee_id": employee_id},
        {"_id": 0}
    )


def get_employee_photo_base64(employee_id):
    """Pull the employee's currently registered photo straight out of
    GridFS (same store registerface.py writes to) and return it as a
    base64 data URL, or None if there isn't one on file."""
    photo = fs.find_one({"employee_id": employee_id})

    if photo is None:
        return None

    encoded = base64.b64encode(photo.read()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"



def generate_qr(content, filename):

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4
    )

    qr.add_data(content)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    path = os.path.join(QR_DIR, filename)
    img.save(path)

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    return path, "data:image/png;base64," + encoded




@qrcodescanner_bp.route("/register-page")
def register_page():
    return render_template("registerface.html")


@qrcodescanner_bp.route("/")
def scanner():
    return render_template("qrcodescanner.html")



@qrcodescanner_bp.route("/employee/<employee_id>")
def employee(employee_id):

    emp = get_employee(employee_id)

    if emp is None:
        return jsonify({
            "success": False,
            "message": "Employee not found"
        }), 404

    return jsonify({
        "success": True,
        "employee_id": employee_id,
        "name": emp.get("name"),
        "contact": emp.get("contact"),
        "address": emp.get("address"),
        "date_hired": emp.get("date_hired"),
        "photo": get_employee_photo_base64(employee_id)
    })