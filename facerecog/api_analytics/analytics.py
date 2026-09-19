import os
from flask import Blueprint, request, send_file

from api_analytics.analytics_export import (
    AnalyticsExportController,
    ensure_commission_table,
    EXPORT_DIR,
)

analytics_export_bp = Blueprint("analytics_export", __name__)
ensure_commission_table()
os.makedirs(EXPORT_DIR, exist_ok=True)


@analytics_export_bp.route("/api/analytics/export", methods=["GET"])
def export_analytics():
    
    
    
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    if not (start_date and end_date):
        return {"error": "start_date at end_date ay required (format: YYYY-MM-DD)."}, 400

    filename = f"analytics_{start_date}_to_{end_date}.xlsx"
    output_path = os.path.join(EXPORT_DIR, filename)

    try:
        AnalyticsExportController.export_analytics_excel(start_date, end_date, output_path)
    except Exception as e:
        return {"error": f"Failed to generate report: {e}"}, 500

    return send_file(output_path, as_attachment=True, download_name=filename)


@analytics_export_bp.route("/api/analytics/commission", methods=["POST"])
def set_commission():
    data = request.get_json(force=True) or {}
    employee_id = (data.get("employeeId") or "").strip()
    employee_name = (data.get("employeeName") or "").strip()
    base_commission = data.get("baseCommission")
    period = (data.get("period") or "monthly").strip()

    if not (employee_id and employee_name and base_commission is not None):
        return {"error": "employeeId, employeeName, at baseCommission ay required."}, 400

    result = AnalyticsExportController.set_commission_agreement(
        employee_id, employee_name, base_commission, period
    )
    return {"commission": result}, 200