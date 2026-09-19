from flask import Blueprint, jsonify, request
from auth.decorators import role_required
from api_controller import ApiController

api_bp = Blueprint("api", __name__)


#  Employees 

@api_bp.route("/api/employees", methods=["GET"])
def get_employees():
    return jsonify(ApiController.get_employees())


@api_bp.route("/api/employees", methods=["POST"])
@role_required("admin")
def add_employee():
    
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required."}), 400

    result, status = ApiController.add_employee(name)
    return jsonify(result), status


@api_bp.route("/api/employees/<employee_id>", methods=["DELETE"])
@role_required("admin")
def delete_employee(employee_id):
   
    result, status = ApiController.soft_delete_employee(employee_id)
    return jsonify(result), status


@api_bp.route("/api/employees/deleted", methods=["GET"])
@role_required("admin")
def list_deleted_employees():
    return jsonify(ApiController.get_deleted_employees())


@api_bp.route("/api/employees/<employee_id>/restore", methods=["POST"])
@role_required("admin")
def restore_employee(employee_id):
    result, status = ApiController.restore_employee(employee_id)
    return jsonify(result), status


@api_bp.route("/api/employees/<employee_id>/photo", methods=["DELETE"])
@role_required("admin")
def delete_employee_photo(employee_id):
    result, status = ApiController.remove_employee_photo(employee_id)
    return jsonify(result), status


#Schedules

@api_bp.route("/api/schedules", methods=["GET"])
def get_schedules():
    return jsonify(ApiController.get_schedules())


@api_bp.route("/api/schedules", methods=["POST"])
@role_required("admin")
def create_schedule():
    data = request.get_json() or {}
    result, status = ApiController.create_schedule(data)
    return jsonify(result), status


@api_bp.route("/api/schedules/<int:schedule_id>/assign", methods=["POST"])
@role_required("admin")
def assign_schedule(schedule_id):
    data = request.get_json() or {}
    employee = (data.get("employee") or "").strip()
    result, status = ApiController.assign_schedule(schedule_id, employee)
    return jsonify(result), status


#  Leaves 

@api_bp.route("/api/leaves", methods=["GET"])
@role_required("admin")
def get_leaves():
    return jsonify(ApiController.get_leaves())


@api_bp.route("/api/leaves", methods=["POST"])
@role_required("admin")
def create_leave():
    data = request.get_json() or {}
    result, status = ApiController.create_leave(data)
    return jsonify(result), status


@api_bp.route("/api/leaves/<int:leave_id>/approve", methods=["POST"])
@role_required("admin")
def approve_leave(leave_id):
    result, status = ApiController.approve_leave(leave_id)
    return jsonify(result), status


@api_bp.route("/api/leaves/<int:leave_id>/decline", methods=["POST"])
@role_required("admin")
def decline_leave(leave_id):
    result, status = ApiController.decline_leave(leave_id)
    return jsonify(result), status


#  Notifications 

@api_bp.route("/api/notifications", methods=["GET"])
@role_required("admin")
def get_notifications():
    return jsonify(ApiController.get_notifications())


@api_bp.route("/api/notifications/mark-all-read", methods=["POST"])
@role_required("admin")
def mark_all_read():
    return jsonify(ApiController.mark_all_read())


#  Location check-ins 

@api_bp.route("/api/location-checkins", methods=["GET"])
@role_required("admin")
def get_location_checkins():
    return jsonify(ApiController.get_location_checkins())


#  Stats 

@api_bp.route("/api/stats", methods=["GET"])
@role_required("admin")
def get_stats():
    return jsonify(ApiController.get_stats())


#  Analytics 

@api_bp.route("/api/analytics", methods=["GET"])
@role_required("admin")
def get_analytics():
    return jsonify(ApiController.get_analytics())


@api_bp.route("/api/log-attendance", methods=["GET"])
@role_required("admin")
def latest_attendance():
    return jsonify(ApiController.latest_attendance())