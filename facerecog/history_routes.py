from flask import jsonify, Blueprint

from auth.decorators import role_required
from registerface_controller import RegisterFaceController 

employee_bp = Blueprint('employee_deleted', __name__)


@employee_bp.route('/api/employees/deleted', methods=['GET'])
@role_required('admin')
def get_deleted_employees():
    
    return jsonify({"deleted": RegisterFaceController.list_deleted_employees()})


@employee_bp.route('/api/employees/<employee_id>/restore', methods=['POST'])
@role_required('admin')
def restore_employee_route(employee_id):
    success = RegisterFaceController.restore_employee(employee_id)
    if not success:
        return jsonify({"error": "No archived record found for this employee."}), 404
    return jsonify({"success": True, "message": "Employee restored successfully."})