import os
import base64
import threading
import cv2
import numpy as np
import qrcode
import uuid
import psycopg2
from io import BytesIO

from database.crud import create_employee, update_employee, get_employee_by_id
from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id
from appwrite.id import ID
from appwrite.input_file import InputFile
from appwrite.query import Query

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_DIR = os.path.join(BASE_DIR, "qrcodes")

os.makedirs(QR_DIR, exist_ok=True)


class RegisterFaceController:

    @staticmethod
    def delete_employee_photo(employee_id, deleted_by=None):
    
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT photo_file_id FROM employees WHERE employee_id = %s",
                (employee_id,)
            )
            row = cur.fetchone()
            photo_file_id = row[0] if row else None
        finally:
            conn.close()

        if row is None:
         
            return False

        if photo_file_id:
            try:
                storage.delete_file(bucket_id=bucket_id, file_id=photo_file_id)
            except Exception as e:
                print(f"Could not delete photo ({photo_file_id}) from Appwrite Storage for {employee_id}: {e}")

        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE employees
                SET is_deleted = true,
                    deleted_at = NOW(),
                    deleted_by = %s
                WHERE employee_id = %s
                """,
                (deleted_by, employee_id)
            )
            conn.commit()
        finally:
            conn.close()

        return True

    @staticmethod
    def list_deleted_employees():
       
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
              
            )
            rows = cur.fetchall()
            columns = [desc[0] for desc in cur.description]
            results = []
            for row in rows:
                record = dict(zip(columns, row))
                if record.get("date_hired"):
                    record["date_hired"] = record["date_hired"].isoformat()
                if record.get("deleted_at"):
                    record["deleted_at"] = record["deleted_at"].isoformat()
                results.append(record)
            return results
        finally:
            conn.close()

    @staticmethod
    def restore_employee(employee_id):
    
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE employees
                SET is_deleted = false,
                    deleted_at = NULL,
                    deleted_by = NULL
                WHERE employee_id = %s AND is_deleted = true
                """,
                (employee_id,)
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    @staticmethod
    def find_existing_employee_id(name):
        
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT employee_id FROM employees WHERE name ILIKE %s LIMIT 1",
                (name.strip(),)
            )
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    @staticmethod
    def save_employee(employee_id, name, contact, address, date_hired, qr_path,
                       photo_file_id=None, photo_data=None, face_registered=None,
                       qr_code_value=None):
     
        conn = get_connection()
        try:
            cur = conn.cursor()
            photo_bytes = psycopg2.Binary(photo_data) if photo_data else None
            cur.execute(
                """
                INSERT INTO employees
                    (employee_id, name, contact, address, date_hired, qr_path,
                     qr_code_value, photo_file_id, photo, photo_data, face_registered)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, false))
                ON CONFLICT (employee_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    contact = EXCLUDED.contact,
                    address = EXCLUDED.address,
                    date_hired = EXCLUDED.date_hired,
                    qr_path = EXCLUDED.qr_path,
                    qr_code_value = COALESCE(EXCLUDED.qr_code_value, employees.qr_code_value),
                    photo_file_id = COALESCE(EXCLUDED.photo_file_id, employees.photo_file_id),
                    photo = COALESCE(EXCLUDED.photo, employees.photo),
                    photo_data = COALESCE(EXCLUDED.photo_data, employees.photo_data),
                    photo_deleted_by_admin = CASE WHEN EXCLUDED.photo_data IS NOT NULL
                    THEN false ELSE employees.photo_deleted_by_admin END,
                    face_registered = COALESCE(EXCLUDED.face_registered, employees.face_registered),
                    created_at = NOW()
                """,
                (employee_id, name, contact, address, date_hired, qr_path,
                 qr_code_value, photo_file_id, photo_bytes, photo_bytes,
                 face_registered)
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_employee(employee_id):
       
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT employee_id, name, contact, address, date_hired, qr_path,
                       qr_code_value, photo_file_id, face_registered, created_at
                FROM employees WHERE employee_id = %s
                """,
                (employee_id,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            columns = [desc[0] for desc in cur.description]
            record = dict(zip(columns, row))

           
            if record.get("date_hired"):
                record["date_hired"] = record["date_hired"].isoformat()

         
            if record.get("created_at"):
                record["created_at"] = record["created_at"].isoformat()

            return record
        finally:
            conn.close()

    @staticmethod
    def save_image_to_storage(data_url, filename, employee_id, name):
   
        img_data = base64.b64decode(data_url.split(",")[1])

        np_arr = np.frombuffer(img_data, np.uint8)
        img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        success, buffer = cv2.imencode(".jpg", img)
        jpeg_bytes = buffer.tobytes()

      
        old_photo_id = None
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT photo_file_id FROM employees WHERE employee_id = %s",
                (employee_id,)
            )
            row = cur.fetchone()
            if row:
                old_photo_id = row[0]
        finally:
            conn.close()

        if old_photo_id:
            try:
                storage.delete_file(bucket_id=bucket_id, file_id=old_photo_id)
            except Exception as e:
                print(f"Could not delete old photo ({old_photo_id}) from Appwrite Storage:", e)

        file_id = None
        try:
            result = storage.create_file(
                bucket_id=bucket_id,
                file_id=ID.unique(),
                file=InputFile.from_bytes(jpeg_bytes, filename=filename)
            )
            file_id = result.id
        except Exception as e:
            print(f"Could not upload photo to Appwrite Storage for {employee_id}:", e)

        return file_id, jpeg_bytes

    @staticmethod
    def generate_qr_code(content, filename):
        """
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

    @classmethod
    def register_employee(cls, image, name, contact, address, date_hired, url_root):
        """
        """
        existing_id = cls.find_existing_employee_id(name)
        is_existing = existing_id is not None
        employee_id = existing_id or uuid.uuid4().hex[:8]

        filename = f"{employee_id}_{uuid.uuid4().hex[:8]}.jpg"
        qr_filename = f"{employee_id}_qr.png"

        photo_file_id, photo_data = cls.save_image_to_storage(image, filename, employee_id, name)

        qr_content = url_root.rstrip("/") + f"/employee/{employee_id}"
        qr_path, qr_data_url = cls.generate_qr_code(qr_content, qr_filename)

        # Huwag munang i-set ang face_registered dito (None = "wag galawin").
        # Ang deepfacerecog_controller.load_known_faces(), na tatawagin sa
        # ibaba, ang siyang magpapasya kung talagang may nakuhang usable face
        # encoding mula sa larawang ito, at doon lang dapat i-flip ang
        # face_registered patungong true/false.
        cls.save_employee(employee_id, name, contact, address, date_hired, qr_path,
                           photo_file_id=photo_file_id, photo_data=photo_data,
                           face_registered=None, qr_code_value=qr_content)

        # PostgreSQL sync (hal. sa auth users table, hiwalay sa employees)
        try:
            if get_employee_by_id(employee_id):
                update_employee(employee_id, name=name)
            else:
                create_employee(employee_id, name)
        except Exception as e:
            print(f"PostgreSQL sync failed for {employee_id}: {e}")

        # I-reload ang known faces list sa deepfacerecog module para makilala
        # ng face scanner ang bagong-register/na-update na tao, kahit hindi
        # pa nire-restart ang buong server. Ito ay re-downloads AT
        # re-encodes ang LAHAT ng litrato ng LAHAT ng employees mula sa
        # Appwrite Storage - kaya kung tatakbo ito nang synchronous (i.e.
        # hinihintay muna bago mag-reply), lumalaki ang oras ng "Saving…"
        # sa browser habang dumadami ang mga naka-register na empleyado.
        # Pinapatakbo natin ito sa isang background thread para agad
        # makabalik ang response sa user, at hindi na kailangang maghintay
        # ang UI habang nire-reload ang buong known-faces list.
        def _reload_known_faces_async():
            try:
                from deepfacerecog_controller import load_known_faces
                load_known_faces()
            except Exception as e:
                print(f"Could not reload known faces after registering {employee_id}: {e}")

        threading.Thread(target=_reload_known_faces_async, daemon=True).start()

        return {
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
        }

    @classmethod
    def get_employee_details(cls, employee_id):
        
        return cls.get_employee(employee_id)