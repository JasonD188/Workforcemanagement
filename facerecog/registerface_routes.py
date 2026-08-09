from flask import Blueprint, render_template, request, jsonify

from registerface_controller import RegisterFaceController

registerface_bp = Blueprint("registerface_bp", __name__)


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

    result = RegisterFaceController.register_employee(
        image=image,
        name=name,
        contact=contact,
        address=address,
        date_hired=date_hired,
        url_root=request.url_root
    )

    return jsonify(result)


@registerface_bp.route("/employee/<employee_id>")
def get_employee(employee_id):
    employee = RegisterFaceController.get_employee_details(employee_id)

    if not employee:
        return jsonify({
            "success": False,
            "message": "Employee not found"
        }), 404

    return jsonify({
        "success": True,
        **employee
    })