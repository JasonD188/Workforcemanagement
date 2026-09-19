import base64
import uuid
from datetime import datetime

import psycopg2
from flask import Flask
from dotenv import load_dotenv
from appwrite.id import ID
from appwrite.input_file import InputFile

from database.crud import get_all_employees, create_employee
from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id

load_dotenv()
app = Flask(__name__)

try:
    conn = get_connection()
    conn.close()
    print("PostgreSQL Connected!")
except Exception as e:
    print("PostgreSQL Connection Error:", e)


def _upload_image(b64_data, filename_prefix):
  
    if not b64_data:
        return None, None
    try:
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        image_bytes = base64.b64decode(b64_data)
        filename = f"{filename_prefix}_{uuid.uuid4().hex}.jpg"

        file_id = None
        try:
            result = storage.create_file(
                bucket_id=bucket_id,
                file_id=ID.unique(),
                file=InputFile.from_bytes(bytes=image_bytes, filename=filename, mime_type="image/jpeg")
            )
            file_id = result.id
        except Exception as upload_err:
            print("Could not upload image to Appwrite Storage:", upload_err)

        return file_id, image_bytes
    except Exception as decode_err:
        print("Could not decode image data:", decode_err)
        return None, None


def load_employees():
    """
    Kinukuha ang lahat ng ACTIVE (hindi is_deleted=true) na employees mula sa
    Postgres. Dati, walang WHERE clause dito - kinukuha LAHAT ng records sa
    employees table, kasama pa ang mga na-soft-delete na (is_deleted=true).
    Dahil ang function na ito ang pinagmumulan ng employee info (pangalan,
    contact, address) na ginagamit ng deepfacerecog_controller.py sa
    face-matching, QR-mismatch messages, at pag-populate ng scan results -
    kahit tama na ang asikaso ng load_known_faces() sa pag-e-exclude ng
    deleted employees sa ENCODING list, ang mga NA-DELETE na employee ay
    puwede pa ring lumabas bilang "matched"/"belongs to" sa mga mensahe,
    dahil dito pa rin sila kinukuha. Idinagdag ang WHERE clause para
    IISANG lugar na lang ang kailangang ayusin, at para ma-siguradong
    lahat ng gumagamit ng load_employees() ay ACTIVE employees lang
    (kagaya ng nakikita sa admin dashboard Employee List) ang makikita.
    """
    employees = {}
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT employee_id, name, contact, address, date_hired "
            "FROM employees "
            "WHERE COALESCE(is_deleted, false) = false"
        )
        for row in cur.fetchall():
            eid, name, contact, address, date_hired = row
            if not eid:
                continue
            employees[eid] = {
                "name": name or "",
                "contact": contact or "",
                "address": address or "",
                "date_hired": date_hired.isoformat() if date_hired else ""
            }
        cur.close()
    finally:
        conn.close()
    return employees


def find_employee_by_name(person_name):
    employees = load_employees()
    matches = [
        (eid, info) for eid, info in employees.items()
        if info.get("name", "").strip().lower() == person_name.strip().lower()
    ]
    if len(matches) > 1:
        print(f"WARNING: {len(matches)} PostgreSQL employee records share the name "
              f"{person_name!r}: {[m[0] for m in matches]}.")
    if matches:
        return matches[0]
    available_names = [info.get("name") for info in employees.values()]
    print(f"No PostgreSQL record found for face-match name {person_name!r}. "
          f"Available employee names: {available_names}")
    return None, None


def log_verified_scan(employee_id, name, score, scanned_b64, registered_b64,
                       scan_type=None, qr_code_image=None, status=None, note=None):
    verified_at = datetime.utcnow()
    inserted_id = None

    
    scanned_file_id, scanned_bytes = _upload_image(scanned_b64, f"{employee_id}_scanned")
    registered_file_id, registered_bytes = _upload_image(registered_b64, f"{employee_id}_registered")
    qr_file_id, qr_bytes = _upload_image(qr_code_image, f"{employee_id}_qr")

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO scan_logs
                (employee_id, name, score, scanned_image_id, registered_image_id,
                 qr_code_image_id, scan_type, status, note, verified_at,
                 scanned_image_data, registered_image_data, qr_code_image_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (employee_id, name, score, scanned_file_id, registered_file_id,
             qr_file_id, scan_type, status, note, verified_at,
             psycopg2.Binary(scanned_bytes) if scanned_bytes else None,
             psycopg2.Binary(registered_bytes) if registered_bytes else None,
             psycopg2.Binary(qr_bytes) if qr_bytes else None)
        )
        inserted_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
    except Exception as log_err:
        print("Could not save scan log to PostgreSQL:", log_err)
        conn.rollback()
    finally:
        conn.close()

    return verified_at, inserted_id


def fetch_recent_scans(limit=200):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM scan_logs ORDER BY verified_at DESC LIMIT %s",
            (limit,)
        )
        columns = [desc[0] for desc in cur.description]
        rows = [dict(zip(columns, row)) for row in cur.fetchall()]
        cur.close()
        return rows
    finally:
        conn.close()


@app.route("/employees", methods=["GET"])
def list_employees():
    
    return {"employees": get_all_employees()}