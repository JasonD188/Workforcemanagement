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


@registerface_bp.route("/debug/<employee_id>")
def debug_employee(employee_id):
    """
    Diagnostic endpoint lamang - hindi ginagamit ng UI. Sinusuri kung saan
    talaga humihinto/nabibigo ang face-matching chain para sa isang
    partikular na employee_id (walang Appwrite upload, nawalang file sa
    storage, walang usable na mukha, o hindi pa na-reload sa memory), sa
    halip na hulaan lang batay sa symptoms sa scan screen.

    Halimbawa: GET /registerface/debug/f9a53db4
    """
    result = RegisterFaceController.diagnose_employee_face(employee_id)
    return jsonify(result)