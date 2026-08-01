from flask import Blueprint, render_template, request, jsonify
import os
import base64
import cv2
import numpy as np
import qrcode
import uuid
from io import BytesIO

from api import client, db, employees_collection, fs

registerface_bp = Blueprint("registerface_bp", __name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_DIR = os.path.join(BASE_DIR, "qrcodes")

os.makedirs(QR_DIR, exist_ok=True)


def find_existing_employee_id(name):
    """Return the employee_id already used for this name, if one exists (MongoDB)."""
    normalized = name.strip().lower()

    doc = employees_collection.find_one({
        "name": {"$regex": f"^{normalized}$", "$options": "i"}
    })

    return doc.get("employee_id") if doc else None


def save_employee(employee_id, name, contact, address, date_hired, qr_path):
    """Upsert this employee's record into MongoDB Atlas (insert new, or
    overwrite existing details for the same employee_id)."""
    employees_collection.update_one(
        {"employee_id": employee_id},
        {"$set": {
            "employee_id": employee_id,
            "name": name,
            "contact": contact,
            "address": address,
            "date_hired": date_hired,
            "qr_path": qr_path
        }},
        upsert=True
    )


def save_image_to_gridfs(data_url, filename, employee_id, name):
    """
    Decode the base64 photo from the browser and store it directly in
    MongoDB GridFS (no local register/ folder anymore).

    Any previously stored photo(s) for this employee_id are deleted first,
    so there is always exactly ONE current registered photo per employee -
    this is what re-registering the same person now overwrites.
    """
    img_data = base64.b64decode(data_url.split(",")[1])

    np_arr = np.frombuffer(img_data, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    success, buffer = cv2.imencode(".jpg", img)
    jpeg_bytes = buffer.tobytes()


    for old_file in fs.find({"employee_id": employee_id}):
        fs.delete(old_file._id)

    file_id = fs.put(
        jpeg_bytes,
        filename=filename,
        employee_id=employee_id,
        name=name,
        content_type="image/jpeg"
    )

    return str(file_id)


def generate_qr_code(content, filename):
    """Generate a QR code image encoding the given content and save it.

    FIX: idinagdag ang os.makedirs dito mismo (hindi lang sa module level
    noong pag-import). Kung na-delete ang qrcodes/ folder habang tumatakbo
    pa rin ang server (halimbawa dahil sa OneDrive sync, antivirus, o
    manual na pagtanggal), ito ang nagre-recreate nito bago i-save ang
    bagong QR - kaya hindi na uulit yung FileNotFoundError.
    """

    os.makedirs(QR_DIR, exist_ok=True)

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )

    qr.add_data(content)
    qr.make(fit=True)

    qr_img = qr.make_image(fill_color="black", back_color="white")

    path = os.path.join(QR_DIR, filename)
    qr_img.save(path)


    buffer = BytesIO()
    qr_img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return path, f"data:image/png;base64,{qr_base64}"


@registerface_bp.route("/")
def index():
    return render_template("registerface.html")


@registerface_bp.route("/register", methods=["POST"])
def register():

    image = request.json["image"]
    name = request.json["name"]
    contact = request.json.get("contact", "")
    address = request.json.get("address", "")
    date_hired = request.json.get("dateHired", "")


    existing_id = find_existing_employee_id(name)
    is_existing = existing_id is not None
    employee_id = existing_id or uuid.uuid4().hex[:8]

    filename = f"{employee_id}_{name}.jpg"
    qr_filename = f"{employee_id}_qr.png"


    save_image_to_gridfs(image, filename, employee_id, name)


    qr_content = request.url_root.rstrip("/") + f"/employee/{employee_id}"

    qr_path, qr_data_url = generate_qr_code(qr_content, qr_filename)

    save_employee(employee_id, name, contact, address, date_hired, qr_path)

    return jsonify({
        "success": True,
        "message": f"{name} updated successfully" if is_existing else f"{name} saved successfully",
        "employee_id": employee_id,
        "name": name,
        "contact": contact,
        "address": address,
        "date_hired": date_hired,
        "qr_code": qr_data_url,
        "qr_filename": qr_filename,
        "is_existing": is_existing
    })


@registerface_bp.route("/employee/<employee_id>")
def get_employee(employee_id):
    employee = employees_collection.find_one(
        {"employee_id": employee_id},
        {"_id": 0}
    )

    if not employee:
        return jsonify({
            "success": False,
            "message": "Employee not found"
        }), 404

    return jsonify({
        "success": True,
        **employee
    })