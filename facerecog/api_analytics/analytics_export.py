from datetime import datetime, time
 
import numpy as np
import pandas as pd
 
from database.postgress import get_connection
 
 
SHIFT_START = time(9, 0)   # 9:00 AM
SHIFT_END = time(18, 0)    # 6:00 PM
 
LATE_DEDUCTION_PCT_PER_10MIN = 2.08
OT_MINIMUM_MINUTES = 30  # minimum minutes past SHIFT_END bago mag-count as OT pwede baguhin 1hour dpende sa client 
LOCATION_MATCH_WINDOW_MINUTES = 15 
 
WORK_DAY_ABBRS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]  # Lunes-Sabado
EXPORT_DIR = "exports"
 
 
def _row_to_dict(cur, row):
    columns = [desc[0] for desc in cur.description]
    return dict(zip(columns, row))
 
 
def ensure_commission_table():
    """ (CREATE TABLE IF NOT EXISTS)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS commission_agreements (
                id SERIAL PRIMARY KEY,
                employee_id VARCHAR NOT NULL,
                employee_name VARCHAR NOT NULL,
                base_commission NUMERIC NOT NULL DEFAULT 0,
                period VARCHAR NOT NULL DEFAULT 'monthly',
                effective_date DATE NOT NULL DEFAULT CURRENT_DATE,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                deleted_at TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()
 
 
class AnalyticsExportController:
 
    @staticmethod
    def set_commission_agreement(employee_id, employee_name, base_commission, period="monthly"):
        # bagong row lagi ito, hindi update - para may history ng dating rates
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO commission_agreements
                    (employee_id, employee_name, base_commission, period, effective_date, created_at)
                VALUES (%s, %s, %s, %s, CURRENT_DATE, %s)
                RETURNING id, employee_id, employee_name, base_commission, period, effective_date, created_at
                """,
                (employee_id, employee_name, base_commission, period, datetime.utcnow())
            )
            row = _row_to_dict(cur, cur.fetchone())
            conn.commit()
        finally:
            conn.close()
        return row
 
    @staticmethod
    def get_latest_commission(employee_id):
        # pinakabagong active agreement lang - ito yung gagamitin as base_commission dagdagan kung may kulang 
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT employee_id, employee_name, base_commission, period, effective_date
                FROM commission_agreements
                WHERE employee_id = %s AND deleted_at IS NULL
                ORDER BY effective_date DESC, created_at DESC
                LIMIT 1
                """,
                (employee_id,)
            )
            row = cur.fetchone()
            return _row_to_dict(cur, row) if row else None
        finally:
            conn.close()
 
    @staticmethod
    def _fetch_scan_logs(start_date, end_date):
        """
        wag pakilaman ito Kumukuha lang ng scan_logs na FULLY VERIFIED - kailangan may
        matching row sa location_checkins (via scan_id, o employee_id +
        window paikot sa verified_at). Parehong batayan ito ng
        _has_location_checkin() sa deepfacerecog_controller.py at
        _is_fully_verified() sa api_controller.py, para magkasundo ang
        Monitoring, Dashboard Stats, at Analytics sa iisang definition
        ng "fully verified". Hindi kasama dito ang mga scan na
        "pending_location" pa lang.
 
        Umaasa sa scan_logs na may scan_type na 'time_in' at 'time_out'.
        Kung 'time_out' ay ibang table/column pa lang sa iyong DB, i-adjust
        ang query na ito.
        """
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT sl.employee_id, sl.name, sl.scan_type, sl.score, sl.verified_at
                FROM scan_logs sl
                WHERE sl.verified_at::date BETWEEN %s AND %s
                  AND EXISTS (
                      SELECT 1 FROM location_checkins lc
                      WHERE lc.scan_id::text = sl.id::text
                         OR (
                             lc.employee_id = sl.employee_id
                             AND lc.created_at BETWEEN sl.verified_at
                                 AND sl.verified_at + INTERVAL '{LOCATION_MATCH_WINDOW_MINUTES} minutes'
                         )
                  )
                ORDER BY sl.verified_at ASC
                """,
                (start_date, end_date)
            )
            return [_row_to_dict(cur, row) for row in cur.fetchall()]
        finally:
            conn.close()
 
    @staticmethod
    def _fetch_leaves():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT name, dates, qr_id, status, created_at, processed_at
                FROM leaves
                WHERE status = 'Approved'
                ORDER BY created_at DESC
                """
            )
            return [_row_to_dict(cur, row) for row in cur.fetchall()]
        finally:
            conn.close()
 
    @staticmethod
    def _fetch_employees():
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT name, employee_id FROM employees WHERE deleted_at IS NULL ORDER BY name ASC"
            )
            return [_row_to_dict(cur, row) for row in cur.fetchall()]
        finally:
            conn.close()
 
    @classmethod
    def build_time_in_out_df(cls, start_date, end_date):
        """Isang row per employee per araw: time_in, time_out, late_minutes, overtime_minutes."""
        logs = cls._fetch_scan_logs(start_date, end_date)
        if not logs:
            return pd.DataFrame(columns=[
                "employee_id", "name", "date", "time_in", "time_out",
                "late_minutes", "overtime_minutes"
            ])
 
        df = pd.DataFrame(logs)
        df["verified_at"] = pd.to_datetime(df["verified_at"])
        df["date"] = df["verified_at"].dt.date
 
        time_in = (
            df[df["scan_type"] == "time_in"]
            .groupby(["employee_id", "name", "date"])["verified_at"]
            .min()
            .rename("time_in")
        )
        time_out = (
            df[df["scan_type"] == "time_out"]
            .groupby(["employee_id", "name", "date"])["verified_at"]
            .max()
            .rename("time_out")
        )
 
        merged = pd.concat([time_in, time_out], axis=1).reset_index()
 
        def _late_minutes(ts):
            # late minutes per day, base sa SHIFT_START (9AM) - ito yung
            # sinusummarize sa build_performance_df para sa deduction
            if pd.isna(ts):
                return np.nan
            shift_start_dt = datetime.combine(ts.date(), SHIFT_START)
            diff_minutes = (ts.to_pydatetime() - shift_start_dt).total_seconds() / 60
            return round(max(0.0, diff_minutes), 2)
 
        def _overtime_minutes(ts):
            # minutes lampas 6PM, may 30-min buffer muna bago mag-count as OT
            if pd.isna(ts):
                return np.nan
            shift_end_dt = datetime.combine(ts.date(), SHIFT_END)
            diff_minutes = (ts.to_pydatetime() - shift_end_dt).total_seconds() / 60
            if diff_minutes < OT_MINIMUM_MINUTES:
                return 0.0
            return round(diff_minutes, 2)
 
        merged["late_minutes"] = merged["time_in"].apply(_late_minutes)
        merged["overtime_minutes"] = merged["time_out"].apply(_overtime_minutes)
        return merged
 
    @classmethod
    def build_attendance_record_df(cls, start_date, end_date):
        """
        Attendance grid: 1 row per employee, 1 column per araw (Lunes-Sabado
        lang) sa loob ng start_date/end_date range.
        Values: 'Present', 'Late', 'Absent'.
        """
        employees = cls._fetch_employees()
        time_df = cls.build_time_in_out_df(start_date, end_date)
 
        all_days = pd.date_range(start_date, end_date, freq="D")
        work_days = [d for d in all_days if d.strftime("%a") in WORK_DAY_ABBRS]
 
        records = []
        for emp in employees:
            row = {"employee_id": emp["employee_id"], "name": emp["name"]}
            for d in work_days:
                day_label = d.strftime("%Y-%m-%d (%a)")
                match = time_df[
                    (time_df["employee_id"] == emp["employee_id"]) &
                    (time_df["date"] == d.date())
                ]
                if match.empty:
                    row[day_label] = "Absent"
                elif (match.iloc[0]["late_minutes"] or 0) > 0:
                    row[day_label] = "Late"
                else:
                    row[day_label] = "Present"
            records.append(row)
 
        return pd.DataFrame(records)
 
    @classmethod
    def build_performance_df(cls, start_date, end_date):
        """
        Performance = base commission minus late deduction (linear, per
        10 mins late = 2.08%, continuous - kaya kahit 5 min late may
        proportional deduction na, hindi naghihintay maabot ang 10 min).
        Isang row per employee. May total_overtime_minutes din, pero
        minutes lang muna - wala pang conversion sa pisong OT pay.
        """
        time_df = cls.build_time_in_out_df(start_date, end_date)
        employees = cls._fetch_employees()
 
        records = []
        for emp in employees:
            # total late niya sa buong range, akumulado (hindi per-araw)
            emp_logs = time_df[time_df["employee_id"] == emp["employee_id"]]
            total_late_minutes = float(emp_logs["late_minutes"].fillna(0).sum())
 
            # total OT niya sa buong range - hiwalay sa commission, minutes lang
            total_overtime_minutes = float(emp_logs["overtime_minutes"].fillna(0).sum())
 
            # 10 min late = 2.08% deduction, naka-cap sa 100%
            deduction_pct = (total_late_minutes / 10) * LATE_DEDUCTION_PCT_PER_10MIN
            deduction_pct = min(deduction_pct, 100.0)
 
            # base niya galing sa pinakabagong commission agreement (0 kung wala pa)
            commission = cls.get_latest_commission(emp["employee_id"])
            base_commission = float(commission["base_commission"]) if commission else 0.0
 
            # ito na yung aktwal na matatanggap niya after deduction
            net_commission = round(base_commission * (1 - deduction_pct / 100), 2)
 
            records.append({
                "employee_id": emp["employee_id"],
                "name": emp["name"],
                "base_commission": base_commission,
                "total_late_minutes": round(total_late_minutes, 2),
                "deduction_pct": round(deduction_pct, 2),
                "net_commission": net_commission,
                "total_overtime_minutes": round(total_overtime_minutes, 2),
            })
 
        return pd.DataFrame(records)
 
    @classmethod
    def build_leave_df(cls):
        leaves = cls._fetch_leaves()
        if not leaves:
            return pd.DataFrame(columns=["name", "dates", "status", "processed_at"])
        return pd.DataFrame(leaves)[["name", "dates", "status", "processed_at"]]
 
    @classmethod
    def export_analytics_excel(cls, start_date, end_date, output_path):
        """
       
        Attendance | Performance | Leave | TimeInOut
        Ito ang direktang i-i-import sa Power BI (Get Data > Excel).
        """
        attendance_df = cls.build_attendance_record_df(start_date, end_date)
        performance_df = cls.build_performance_df(start_date, end_date)
        leave_df = cls.build_leave_df()
        time_df = cls.build_time_in_out_df(start_date, end_date)
 
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            attendance_df.to_excel(writer, sheet_name="Attendance", index=False)
            performance_df.to_excel(writer, sheet_name="Performance", index=False)
            leave_df.to_excel(writer, sheet_name="Leave", index=False)
            time_df.to_excel(writer, sheet_name="TimeInOut", index=False)
 

        return output_path
        #iintegrate din ang sa payroll  since naka indicate narin ang attendance logs at overtime
        #commission agreement bawat employee iintegrate din ito ibabase ko ito sa deduction 
        #kelangan ko makuha ang data 