

from flask import session, jsonify
from auth.decorators import role_required
from registerface_controller import RegisterFaceController
from api_routes import api_bp 



@api_bp.route("/employees/<employee_id>/photo", methods=["DELETE"])
@role_required("admin")
def delete_employee_photo_route(employee_id):
  
    deleted_by = session.get("employee_id", "unknown_admin")

    RegisterFaceController.delete_employee_photo(employee_id, deleted_by=deleted_by)

    return jsonify({
        "success": True,
        "message": f"Photo ni {employee_id} ay tinanggal ni {deleted_by}."
    })
