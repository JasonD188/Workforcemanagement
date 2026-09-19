import os
import base64
import threading
import re
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
                       qr_code_value, photo_file_id, face_registered, is_deleted,
                       created_at
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

    @staticmethod
    def _check_face_verifiable(photo_data):
        """
        Sinusuri kaagad (synchronous, iisang litrato lang naman kaya mabilis)
        kung usable ang bagong-upload na larawan para sa face scanner, gamit
        ang IISANG paraan ng detection (encode_single_face sa
        deepfacerecog_controller.py) na ginagamit din ng load_known_faces()
        AT ng scan_face() - mas malakas kaysa sa dating "default lang, walang
        upsampling" na ginagamit dati dito sa registration path.

        NOTE: Ito ay checking lang ng mukha mismo sa larawan (photo_data,
        raw bytes mula sa camera). HINDI nito sinusuri kung matagumpay ang
        Appwrite Storage upload - dahil dun mismo kumukuha ang
        load_known_faces() ng mga litratong ie-encode para sa scanning.
        Kaya HUWAG gamitin ang return value nito nang mag-isa bilang
        panghuling "face_verified" - kailangan pa rin i-AND sa
        photo_file_id (tingnan sa register_employee).

        Returns: (face_found: bool, n_faces_found: int)
        """
        try:
            from deepfacerecog_controller import encode_single_face

            np_arr = np.frombuffer(photo_data, np.uint8)
            img_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if img_bgr is None:
                return False, 0

            rgb_image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            encoding, n_faces = encode_single_face(rgb_image)

            return encoding is not None, n_faces
        except Exception as e:
            print(f"Could not run synchronous face-verifiability check: {e}")
            # Hindi natin alam kung usable ang litrato - huwag magsinungaling
            # na "verified" ito. Ituring itong hindi pa verified, at pababayaan
            # na lang ang background load_known_faces() na mag-reconcile sa
            # tunay na estado (via face_registered column) sa susunod na reload.
            return False, -1

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

        # Kung nabigo ang pag-upload papuntang Appwrite Storage,
        # save_image_to_storage() ay nagbabalik ng photo_file_id=None (may
        # naka-print nang error mula roon). Napakahalaga nito dahil ang
        # load_known_faces() - ang bumubuo sa known_face_encodings list na
        # sinusuri ng scan_face() - ay EXCLUSIVELY kumukuha ng mga litrato
        # mula sa Appwrite Storage bucket, HINDI mula sa Postgres photo_data
        # blob. Kaya kahit malinaw at may nakitang isang mukha ang litrato
        # (face_found=True), kung walang file sa Appwrite bucket, walang
        # ma-e-encode para sa employee na ito kailanman - permanenteng
        # "walang registered face" sa scan_face(), hanggang matagumpay ang
        # upload sa susunod na re-register.
        appwrite_upload_ok = photo_file_id is not None

        qr_content = url_root.rstrip("/") + f"/employee/{employee_id}"
        qr_path, qr_data_url = cls.generate_qr_code(qr_content, qr_filename)

        # Kaagad na sinusuri (synchronous) kung talagang may usable na
        # nakuhang face encoding mula sa litratong ito - parehong
        # detection settings ang ginagamit dito at sa scan, kaya kung
        # papasa ito rito, dapat papasa rin sa scan_face() mismo.
        face_found, n_faces = cls._check_face_verifiable(photo_data)

        # face_verified ay TRUE lamang kapag PAREHONG (a) may malinaw at
        # iisang mukha ang nakita SA litrato, AT (b) matagumpay itong
        # na-upload sa Appwrite Storage - dahil kailangan pareho para
        # aktwal na ma-match ito ng scan_face() sa totoong paggamit.
        face_verified = face_found and appwrite_upload_ok

        warning = None
        if not appwrite_upload_ok:
            warning = ("Nai-save ang litrato pero NABIGO ang pag-sync sa storage "
                       "server (posibleng internet/connection issue). Kahit "
                       "malinaw ang mukha, HINDI pa ito magagamit ng face scanner "
                       "hangga't hindi ito muling nire-register nang matagumpay ang "
                       "pag-sync. Subukan ulit i-register.")
        elif not face_found:
            if n_faces == 0:
                warning = ("Walang malinaw na nakitang mukha sa litrato. "
                           "Subukang ilapit ang mukha sa camera, dagdagan ang ilaw, "
                           "at i-register ulit.")
            elif n_faces > 1:
                warning = (f"{n_faces} mukha ang nakita sa litrato - kailangang "
                           f"iisang mukha lang. I-register ulit gamit ang litratong "
                           f"iisa lang ang mukha.")
            else:
                warning = ("Hindi ma-verify ang mukha sa litratong ito ngayon. "
                           "I-register ulit kung kinakailangan.")

        cls.save_employee(employee_id, name, contact, address, date_hired, qr_path,
                           photo_file_id=photo_file_id, photo_data=photo_data,
                           face_registered=face_verified, qr_code_value=qr_content)

        # PostgreSQL sync (hal. sa auth users table, hiwalay sa employees)
        try:
            if get_employee_by_id(employee_id):
                update_employee(employee_id, name=name)
            else:
                create_employee(employee_id, name)
        except Exception as e:
            print(f"PostgreSQL sync failed for {employee_id}: {e}")

        # I-reload pa rin ang buong known faces list sa deepfacerecog module
        # sa background, para makilala rin ng face scanner ang ibang
        # litratong na-update kamakailan (hal. galing sa admin panel), at
        # para manatiling tama ang face_registered flags ng LAHAT ng
        # employees - hindi lang nitong isang bagong-register. Ito ay
        # re-downloads AT re-encodes ang LAHAT ng litrato mula sa Appwrite
        # Storage kaya mabagal ito kapag dumadami ang mga employees; kaya
        # itinatakbo pa rin ito sa hiwalay na thread para hindi na
        # kailangang maghintay ang response na ito rito.
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
            "is_existing": is_existing,
            "face_verified": face_verified,
            "warning": warning
        }

    @classmethod
    def diagnose_employee_face(cls, employee_id):
        """
        Diagnostic lamang - hindi ito ginagamit ng normal na registration/scan
        flow. Sinusuri nang end-to-end kung bakit hindi na-ma-match ang isang
        partikular na employee sa scanner, sa pamamagitan ng pag-check ng bawat
        posibleng break point nang paisa-isa:

          1. May record ba talaga sa Postgres para dito?
          2. May photo_file_id ba naka-save (successful ang Appwrite upload)?
          3. Ma-download ba mismo ang file na 'yon mula sa Appwrite Storage
             (baka may reference sa DB pero wala na/nabura na ang file)?
          4. Kapag na-decode, may nakita bang usable/iisang mukha (parehong
             encode_single_face na ginagamit ng load_known_faces at scan_face)?
          5. Nasa KASALUKUYANG in-memory na known_face_employee_ids list ba
             ito - ang aktwal na listahang tinitingnan ng scan_face() sa
             real time (baka OK na ang lahat pero hindi pa na-reload)?
        """
        record = cls.get_employee(employee_id)
        if not record:
            return {
                "success": True,
                "employee_id": employee_id,
                "diagnosis": ("Walang record sa Postgres na may employee_id na ito. "
                             "Baka mali ang na-scan na QR, o na-delete na ang employee.")
            }

        photo_file_id = record.get("photo_file_id")
        result = {
            "employee_id": employee_id,
            "name": record.get("name"),
            "is_deleted": record.get("is_deleted"),
            "face_registered_flag_in_db": record.get("face_registered"),
            "photo_file_id": photo_file_id,
        }

        if not photo_file_id:
            deleted_note = (
                " (Deleted na rin ito sa admin dashboard - is_deleted=true - "
                "kaya hindi na ito dapat ma-match kahit paano pa.)"
                if record.get("is_deleted") else ""
            )
            result["diagnosis"] = (
                "BREAK POINT: Walang photo_file_id sa DB. Ibig sabihin, hindi "
                "kailanman successfully na-upload ang litrato ng employee na ito "
                "sa Appwrite Storage (nabigo ang storage.create_file() noong "
                "pag-register). Walang ma-lo-load na encoding para dito kahit "
                "ilang beses pang i-re-register, hangga't hindi gumagana ang "
                f"koneksyon sa Appwrite. I-check ang network/Appwrite config, "
                f"tapos i-re-register.{deleted_note}"
            )
            return {"success": True, **result}

        try:
            file_bytes = storage.get_file_download(bucket_id=bucket_id, file_id=photo_file_id)
        except Exception as e:
            result["diagnosis"] = (
                f"BREAK POINT: May photo_file_id ({photo_file_id}) sa DB, pero "
                f"HINDI ito ma-download mula sa Appwrite Storage ({e}). Ibig "
                f"sabihin na-delete o nawala ang file sa bucket mismo kahit "
                f"nandiyan pa ang reference sa Postgres (orphaned reference). "
                f"Kailangang i-re-register ulit."
            )
            return {"success": True, **result}

        np_arr = np.frombuffer(file_bytes, np.uint8)
        img_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if img_bgr is None:
            result["diagnosis"] = (
                "BREAK POINT: Na-download ang file mula sa Appwrite pero hindi "
                "ito ma-decode bilang valid na image (posibleng sira/corrupted "
                "ang file). I-re-register ulit."
            )
            return {"success": True, **result}

        # NEW: kunin din ang AKTWAL na filename mula sa Appwrite Storage para
        # dito mismo, at i-run ang PAREHONG parsing logic na ginagamit ng
        # load_known_faces() para malaman ang employee_id mula sa filename.
        # Importante ito dahil ang download sa itaas ay gumagamit lang ng
        # photo_file_id (kaya laging gagana kahit ano ang filename) - pero
        # ang load_known_faces() mismo ay UMAASA sa FORMAT ng filename
        # ({employee_id}_{something}.ext) para malaman kung kaninong
        # employee_id ito. Kung magkaiba ang employee_id na nabuo mula sa
        # filename parsing kumpara sa totoong employee_id, ITO na ang dahilan
        # kung bakit hindi na-a-attribute nang tama ang encoding na ito kay
        # employee_id, kahit valid at successfully naka-encode ang litrato.
        try:
            file_meta = storage.get_file(bucket_id=bucket_id, file_id=photo_file_id)
            actual_filename = getattr(file_meta, "name", None)
        except Exception as e:
            actual_filename = None
            result["filename_lookup_error"] = str(e)

        result["actual_filename_in_appwrite"] = actual_filename

        parsed_employee_id_from_filename = None
        if actual_filename:
            base_name = os.path.splitext(actual_filename)[0]
            parts = base_name.split("_", 1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{6,32}", parts[0]):
                parsed_employee_id_from_filename = parts[0]

        result["employee_id_parsed_from_filename"] = parsed_employee_id_from_filename
        result["filename_parsing_matches_employee_id"] = (
            parsed_employee_id_from_filename == employee_id
        )

        if actual_filename and parsed_employee_id_from_filename != employee_id:
            result["diagnosis"] = (
                f"BREAK POINT NAHANAP: Ang aktwal na filename ng litrato sa "
                f"Appwrite ay '{actual_filename}'. Kapag pinarse ito gamit ang "
                f"parehong logic ng load_known_faces() (parts[0] bago ang "
                f"unang '_', dapat 6-32 hex chars), ang nakukuhang employee_id "
                f"ay '{parsed_employee_id_from_filename}' - HINDI tumutugma sa "
                f"totoong employee_id na '{employee_id}'. Kaya kahit valid at "
                f"successfully na-encode ang litrato, hindi ito na-a-attribute "
                f"nang tama sa employee_id na ito sa listahan ng scanner. "
                f"Kailangang i-rename ang file sa Appwrite Storage para magsimula "
                f"sa '{employee_id}_', O i-re-register ulit ang employee na ito "
                f"(dahil ang bagong save_image_to_storage() ay gumagawa na ng "
                f"tamang filename convention)."
            )
            return {"success": True, **result}

        from deepfacerecog_controller import encode_single_face, known_face_employee_ids

        rgb_image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        encoding, n_faces = encode_single_face(rgb_image)
        result["faces_found_in_stored_photo"] = n_faces
        result["encoding_successful"] = encoding is not None
        result["currently_loaded_in_memory_for_scanning"] = employee_id in known_face_employee_ids

        # NEW: sinusuri kung ang employee_id na ito ay KASAMA sa dict na
        # ibinabalik ng load_employees() (mula sa api.py) - dahil ito mismo
        # ang ginagamit ng load_known_faces() bilang FILTER bago pa man
        # subukang i-encode ang litrato. Kung wala rito ang employee_id
        # (kahit nandiyan siya sa `employees` table), aalisin/i-sskip siya
        # sa load_known_faces() - "employee_id no longer exists" - kahit
        # perfectly valid ang litrato niya, at ITO mismo ang idadahilan
        # kung bakit hindi siya makikita sa known_face_employee_ids kahit
        # matagumpay ang encoding kapag chineck nang hiwalay dito.
        result["present_in_load_employees_result"] = None
        try:
            from api import load_employees
            current_employees = load_employees()
            result["present_in_load_employees_result"] = employee_id in current_employees
            if employee_id not in current_employees:
                result["diagnosis"] = (
                    f"BREAK POINT NAHANAP: Valid at successfully na-encode ang "
                    f"litrato, TAMA rin ang filename parsing - PERO ang "
                    f"employee_id na '{employee_id}' ay HINDI kasama sa listahan "
                    f"na ibinabalik ng load_employees() (mula sa api.py). Ang "
                    f"load_known_faces() ay may filter na nag-a-EXCLUDE ng "
                    f"anumang employee_id na wala sa listahang ito (para sa "
                    f"deleted employees) - kaya nag-a-'Skipping...' ito para "
                    f"dito, kahit valid ang litrato. I-check kung tama ba ang "
                    f"laman/query ng load_employees() sa api.py - posibleng may "
                    f"mismatch sa is_deleted flag, ibang column filter, o "
                    f"na-cache/stale na snapshot ng employees list."
                )
                return {"success": True, **result}
        except Exception as e:
            result["load_employees_check_error"] = str(e)

        # NEW: sinusuri kung ang file_id na ito ay AKTWAL na lumalabas sa
        # storage.list_files(bucket_id=bucket_id) - ang PAREHONG tawag na
        # ginagamit ng load_known_faces() para makuha ang lahat ng litrato.
        # Kung walang explicit na pagination/limit parameter ang tawag na
        # ito (gaya ng ginagamit sa kasalukuyan), ang Appwrite SDK ay
        # kadalasang nagbabalik lang ng UNANG PAGE ng resulta (default
        # limit, kadalasan 25 files). Kung marami nang litrato/files sa
        # buong bucket, posibleng hindi na kasama ang file na ito sa
        # unang page - kaya kahit perfectly valid ito (per direct
        # get_file_download check sa itaas), hindi ito kailanman
        # naaabot ng loop sa load_known_faces(), dahil hindi ito
        # nakikita ng list_files() mismo.
        try:
            from deepfacerecog_controller import _list_all_storage_files
            all_storage_files = _list_all_storage_files()
            listed_file_ids = [f.id for f in all_storage_files]
            result["total_files_returned_by_list_files"] = len(listed_file_ids)
            result["file_appears_in_list_files_result"] = photo_file_id in listed_file_ids

            # Appwrite list_files() (isang page lang) ay may `total` attribute
            # na nagsasabi ng TUNAY na bilang ng files sa buong bucket - kunin
            # ito nang hiwalay (isang page lang, walang encoding) para lang
            # ma-display, kahit gumagamit na tayo ng pagination sa itaas.
            try:
                single_page = storage.list_files(bucket_id=bucket_id)
                total_in_bucket = getattr(single_page, "total", None)
            except Exception:
                total_in_bucket = None
            result["total_files_in_bucket_per_appwrite"] = total_in_bucket

            if not result["file_appears_in_list_files_result"]:
                result["diagnosis"] = (
                    f"BREAK POINT NAHANAP: Ang file_id na '{photo_file_id}' "
                    f"(litrato ni {employee_id}) ay VALID at ma-do-download "
                    f"direkta gamit ang file_id nito - PERO HINDI pa rin ito "
                    f"lumalabas kahit gumamit na ng full pagination. Hindi ito "
                    f"dapat mangyari maliban kung na-delete ang file sa pagitan "
                    f"ng dalawang tawag, o may ibang isyu sa Appwrite Storage "
                    f"query. I-double check sa Appwrite console."
                )
                return {"success": True, **result}
            else:
                result["diagnosis"] = (
                    "OK na ngayon - lumalabas na ang file sa buong (paginated) "
                    "listing ng Appwrite Storage. Kung nag-reload ka na ng "
                    "load_known_faces() gamit ang bagong pagination fix, dapat "
                    "gumana na ang pag-match para sa employee na ito."
                )
        except Exception as e:
            result["list_files_check_error"] = str(e)

        if encoding is None:
            result["diagnosis"] = (
                f"BREAK POINT: Na-download at na-decode ang litrato mula sa "
                f"Appwrite, pero {n_faces} mukha lang ang nakita ng "
                f"face_recognition sa stored na litrato (kailangang iisa). "
                f"I-re-register gamit ang mas malinaw/frontal na litrato."
            )
        elif not result["currently_loaded_in_memory_for_scanning"]:
            result["diagnosis"] = (
                "BREAK POINT: Valid ang litrato at successful ang encoding - "
                "pero HINDI pa ito nasa KASALUKUYANG in-memory na listahan na "
                "ginagamit ng scan_face() sa real time. Kailangang i-reload "
                "(tawagin ang POST /reload-faces, o i-restart ang server) "
                "para makuha ito."
            )
        else:
            result["diagnosis"] = (
                "Walang nakitang break point - valid ang litrato, successful "
                "ang encoding, at nasa in-memory list na ito. Dapat gumagana "
                "na ang pag-match. Kung hindi pa rin, posibleng issue na ito "
                "sa mismong QUALITY ng SCAN frame (liveness/mouth-ratio, "
                "lighting, anggulo) sa halip na sa registration."
            )

        return {"success": True, **result}

    @classmethod
    def get_employee_details(cls, employee_id):
        
        return cls.get_employee(employee_id)