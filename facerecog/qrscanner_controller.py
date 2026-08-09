import os
import base64
import qrcode
from io import BytesIO

from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_DIR = os.path.join(BASE_DIR, "qrcodes")

os.makedirs(QR_DIR, exist_ok=True)


class QRCodeScannerController:
    """
    """

    @staticmethod
    def get_employee(employee_id):
        """Fetch an employee record from PostgreSQL."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT name, contact, address, date_hired, photo_file_id
                FROM employees
                WHERE employee_id = %s
                """,
                (employee_id,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cur.description]
            return dict(zip(columns, row))
        finally:
            conn.close()

    @staticmethod
    def get_employee_photo_base64(photo_file_id):
       
        if not photo_file_id:
            return None

        try:
            file_bytes = storage.get_file_download(bucket_id=bucket_id, file_id=photo_file_id)
        except Exception as e:
            print(f"Could not load photo from Appwrite Storage ({photo_file_id}):", e)
            return None

        encoded = base64.b64encode(file_bytes).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"

    @staticmethod
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

    @classmethod
    def get_employee_details(cls, employee_id):
        """"""
        emp = cls.get_employee(employee_id)

        if emp is None:
            return None

        return {
            "success": True,
            "employee_id": employee_id,
            "name": emp.get("name"),
            "contact": emp.get("contact"),
            "address": emp.get("address"),
            "date_hired": emp.get("date_hired"),
            "photo": cls.get_employee_photo_base64(emp.get("photo_file_id"))
        }