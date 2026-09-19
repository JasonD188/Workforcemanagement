import face_recognition
import numpy as np
import base64
import cv2
import os
import re
from datetime import timezone, timedelta

from api_analytics.analytics_export import (
    AnalyticsExportController,
    ensure_commission_table,
    EXPORT_DIR,
)
from api import (
    load_employees,
    find_employee_by_name,
    log_verified_scan,
    fetch_recent_scans,
)
from config.appwrite_config import storage, bucket_id
from database.postgress import get_connection
from appwrite.query import Query

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QR_DIR = os.path.join(BASE_DIR, "qrcodes")


MIN_ACCESS_SCORE_PERCENT = 54  # verify ng percentage ng scan face
TOLERANCE = 1 - (MIN_ACCESS_SCORE_PERCENT / 100)
MIN_MARGIN = 0.07

REGISTER_NUM_JITTERS = 10
SCAN_NUM_JITTERS = 5

# Kailangan tumugma sa LOCATION_MATCH_WINDOW_MINUTES sa api_controller.py
# para magkasundo ang Analytics (_is_fully_verified) at Monitoring (get_scan_logs)
# sa kanilang definition ng "fully verified".
LOCATION_MATCH_WINDOW_MINUTES = 15

ENCODING_MODEL = "small"

SMILE_GROWTH_THRESHOLD = 1.10


LOW_LIGHT_BRIGHTNESS_THRESHOLD = 90


known_face_encodings = []
known_face_names = []
known_face_employee_ids = []
known_face_images = {}


def _list_all_storage_files():
    """
    Kinukuha ang LAHAT ng files sa Appwrite Storage bucket, hindi lang ang
    unang page. Ang default na storage.list_files(bucket_id=bucket_id) na
    WALANG explicit na queries/limit ay basta na lang nagbabalik ng UNANG
    PAGE ng resulta (default limit ng Appwrite - karaniwan ay 25 files).
    Dati, ito ang dahilan kung bakit permanenteng "walang registered face"
    ang mga huling-huling naka-register na employees paglampas sa 25th
    file sa buong bucket - hindi dahil sira ang litrato nila, kundi dahil
    hindi na sila nakikita ng list_files() call mismo.

    Ginagamit dito ang cursor-based pagination ng Appwrite (Query.limit +
    Query.cursor_after) para tiyakin na LAHAT ng files, kahit ilan pa ito,
    ay makukuha - hindi lang ang unang 25.
    """
    all_files = []
    last_id = None
    page_size = 100

    while True:
        queries = [Query.limit(page_size)]
        if last_id:
            queries.append(Query.cursor_after(last_id))

        page = storage.list_files(bucket_id=bucket_id, queries=queries)
        batch = page.files

        if not batch:
            break

        all_files.extend(batch)

        if len(batch) < page_size:
            break

        last_id = batch[-1].id

    return all_files


def _get_active_employee_ids():
    """
    Kinukuha DIREKTA mula sa Postgres ang set ng employee_id na HINDI
    naka-mark na `is_deleted = true` - ito mismo ang parehong `is_deleted`
    column na ginagamit ng delete_employee_photo() at restore_employee()
    sa registerface_controller.py, kaya ito ang pinaka-maaasahang basehan
    ng "nasa admin dashboard employee list pa" (hindi deleted).

    Ginagamit ito bilang FILTER sa load_known_faces() sa halip na umasa
    lamang sa load_employees() (mula sa api.py) - dahil hindi tiyak kung
    ang function na 'yon ay talagang nag-e-exclude ng mga is_deleted=true
    na records. Kung hindi ito ni-filter doon, ang mga litrato ng
    na-delete na employees (na maaaring naiwan pa sa Appwrite Storage dahil
    nabigo ang storage delete noong pag-delete) ay MAAARING MAKA-MATCH pa
    rin sa scan_face() - mali dahil dapat lang ang mga ACTIVE na employees
    sa admin dashboard ang puwedeng ma-match.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT employee_id FROM employees "
            "WHERE COALESCE(is_deleted, false) = false"
        )
        return {row[0] for row in cur.fetchall()}
    except Exception as e:
        print(f"Could not fetch active employee ids from Postgres: {e}")
        return None
    finally:
        conn.close()


def enhance_low_light(bgr_image):
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    mean_brightness = gray.mean()

    if mean_brightness >= LOW_LIGHT_BRIGHTNESS_THRESHOLD:
        return bgr_image

    lab = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_enhanced = clahe.apply(l_channel)

    enhanced_lab = cv2.merge((l_enhanced, a_channel, b_channel))
    enhanced_bgr = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

    print(f"Low-light frame detected (avg brightness={mean_brightness:.1f}) "
          f"- applied CLAHE enhancement before face detection.")

    return enhanced_bgr


def encode_single_face(rgb_image, num_jitters=REGISTER_NUM_JITTERS):
    """
    Ginagamit ito ng registration (register_employee sa
    registerface_controller.py) AT ng load_known_faces() dito sa ibaba, sa
    IISANG paraan lang, para consistent ang detection strictness sa buong
    app. Dati, mas mahina/default lang ang face_recognition detection sa
    registration path (walang upsampling), kaysa sa scan_face() na may
    hog + upsample=2 na fallback pa sa upsample=3. Kaya may mga litratong
    pumapasa sa browser-side na tinyFaceDetector (registerface.html) pero
    nabibigo pala nang tahimik sa aktwal na face_recognition encoding step
    - at dahil walang face_verified na ibinabalik dati sa response, laging
    "✓ saved successfully" pa rin ang lumalabas sa user kahit hindi pa
    talaga usable ang litrato para sa face scanner.

    Returns: (encoding_or_None, num_faces_found)
    """
    face_locations = face_recognition.face_locations(
        rgb_image, model="hog", number_of_times_to_upsample=2
    )
    if len(face_locations) == 0:
        face_locations = face_recognition.face_locations(
            rgb_image, model="hog", number_of_times_to_upsample=3
        )

    if len(face_locations) != 1:
        return None, len(face_locations)

    encodings = face_recognition.face_encodings(
        rgb_image,
        face_locations,
        num_jitters=num_jitters,
        model=ENCODING_MODEL
    )

    if len(encodings) != 1:
        return None, len(face_locations)

    return encodings[0], 1


def mouth_width(landmarks):
    points = landmarks.get("top_lip", []) + landmarks.get("bottom_lip", [])
    if not points:
        return 0.0
    leftmost = min(points, key=lambda p: p[0])
    rightmost = max(points, key=lambda p: p[0])
    return ((rightmost[0] - leftmost[0]) ** 2 + (rightmost[1] - leftmost[1]) ** 2) ** 0.5


def eye_distance(landmarks):
    left_eye = landmarks.get("left_eye", [])
    right_eye = landmarks.get("right_eye", [])
    if not left_eye or not right_eye:
        return 0.0
    lx = sum(p[0] for p in left_eye) / len(left_eye)
    ly = sum(p[1] for p in left_eye) / len(left_eye)
    rx = sum(p[0] for p in right_eye) / len(right_eye)
    ry = sum(p[1] for p in right_eye) / len(right_eye)
    return ((rx - lx) ** 2 + (ry - ly) ** 2) ** 0.5


def get_qr_code_data_url(employee_id):
    if not employee_id:
        return None

    qr_path = os.path.join(QR_DIR, f"{employee_id}_qr.png")
    if not os.path.exists(qr_path):
        return None

    with open(qr_path, "rb") as f:
        qr_bytes = f.read()

    return "data:image/png;base64," + base64.b64encode(qr_bytes).decode("utf-8")


def _sync_face_registered_flags(successfully_registered_ids):

    conn = get_connection()
    try:
        cur = conn.cursor()

        cur.execute("UPDATE employees SET face_registered = false")

        unique_ids = sorted({eid for eid in successfully_registered_ids if eid})
        if unique_ids:
            cur.execute(
                "UPDATE employees SET face_registered = true WHERE employee_id = ANY(%s)",
                (unique_ids,)
            )
        conn.commit()
    except Exception as e:
        print(f"Could not sync face_registered flags to PostgreSQL: {e}")
        conn.rollback()
    finally:
        conn.close()


def load_known_faces():

    global known_face_encodings, known_face_names, known_face_employee_ids, known_face_images

    new_encodings = []
    new_names = []
    new_employee_ids = []
    new_images = {}
    successfully_registered_ids = []

    print("Loading registered faces from Appwrite Storage...")

    # Only faces belonging to employees that currently exist in PostgreSQL
    # AT hindi naka-mark na is_deleted=true ang eligible na i-load/i-match.
    # Ginagamit natin ang DALAWANG source dito:
    #   1. current_employees (load_employees) - para sa contact/address/etc.
    #      data na kailangan pa rin sa ibang parte ng module.
    #   2. active_employee_ids (direktang query sa is_deleted column) - ang
    #      TUNAY na basehan kung sino ang dapat pa ring puwedeng ma-match
    #      sa face scanner, dahil ito mismo ang column na ginagamit ng
    #      admin dashboard (list_deleted_employees, restore_employee, atbp).
    #      Kung NULL ang ibinalik nito (nabigo ang query), babalik tayo sa
    #      current_employees na lang bilang fallback - mas mabuti pang
    #      may ma-load kaysa mag-crash, pero normal case ay dapat gumana ito.
    current_employees = load_employees()
    active_employee_ids = _get_active_employee_ids()
    if active_employee_ids is None:
        active_employee_ids = set(current_employees.keys())

    try:
        storage_files = _list_all_storage_files()
        print(f"Fetched {len(storage_files)} total file(s) from Appwrite Storage "
              f"(across all pages).")
    except Exception as e:
        print("Could not list files from Appwrite Storage:", e)
        storage_files = []

    for storage_file in storage_files:
        try:
            file_id = storage_file.id
            filename = storage_file.name or ""

            employee_id_from_file = None
            person_name = None

            base_name = os.path.splitext(filename or "")[0]
            parts = base_name.split("_", 1)
            if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{6,32}", parts[0]):
                employee_id_from_file = parts[0]
                person_name = parts[1]
            else:
                person_name = base_name or "Unknown"

            # Skip stale photos for employees no longer in PostgreSQL, OR
            # na naka-mark bilang is_deleted=true (naka-delete na sa admin
            # dashboard) - bago pa man gumawa ng anumang network download /
            # decode / face-encoding work.
            if employee_id_from_file and employee_id_from_file not in active_employee_ids:
                reason = (
                    "no longer exists in employees table"
                    if employee_id_from_file not in current_employees
                    else "is marked is_deleted=true (deleted in admin dashboard)"
                )
                print(f"Skipping Appwrite file {filename} - employee_id "
                      f"{employee_id_from_file} {reason}.")
                continue

            file_bytes = storage.get_file_download(bucket_id=bucket_id, file_id=file_id)

            np_arr = np.frombuffer(file_bytes, np.uint8)
            img_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if img_bgr is None:
                print(f"Skipped Appwrite file {filename} (could not decode image)")
                continue

            image_key = employee_id_from_file or person_name
            if image_key not in new_images:
                new_images[image_key] = (
                    "data:image/jpeg;base64," + base64.b64encode(file_bytes).decode("utf-8")
                )

            rgb_image = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

            encoding, n_faces = encode_single_face(rgb_image, num_jitters=REGISTER_NUM_JITTERS)

            if encoding is None:
                print(f"No usable face encoding for Appwrite file {filename} "
                      f"(faces found: {n_faces}) - photo will still display, "
                      f"but this person cannot be face-matched until re-registered "
                      f"with a clearer photo.")
                continue

            new_encodings.append(encoding)

            # Gamitin ang TUNAY na pangalan mula sa Postgres employees.name
            # column (kung available) sa halip na ang person_name na
            # naka-parse lang mula sa filename (na maaaring random hex
            # suffix mula sa uuid.uuid4().hex[:8] na ginagamit ng
            # save_image_to_storage() - hindi tunay na pangalan ng tao,
            # kaya kung ito lang ang gagamitin, lumalabas na "594eec33"
            # bilang "pangalan" imbes na aktwal na pangalan ng empleyado.
            real_info = current_employees.get(employee_id_from_file) if employee_id_from_file else None
            display_name = real_info.get("name") if real_info and real_info.get("name") else person_name

            new_names.append(display_name)
            new_employee_ids.append(employee_id_from_file)

            if employee_id_from_file:
                successfully_registered_ids.append(employee_id_from_file)

        except Exception as e:
            print(f"Error loading Appwrite file {getattr(storage_file, 'name', '?')}: {e}")

    known_face_encodings = new_encodings
    known_face_names = new_names
    known_face_employee_ids = new_employee_ids
    known_face_images = new_images

    _sync_face_registered_flags(successfully_registered_ids)

    print("Loaded faces:", sorted(set(known_face_names)))


_startup_employees = load_employees()
print(f"Loaded {len(_startup_employees)} employee record(s) from PostgreSQL")
for _eid, _info in _startup_employees.items():
    print(f"  - id={_eid} name={_info.get('name')!r}")

load_known_faces()


def best_match_per_employee(face_encoding):
    distances = face_recognition.face_distance(known_face_encodings, face_encoding)

    best_per_key = {}
    for dist, cand_name, cand_eid in zip(distances, known_face_names, known_face_employee_ids):
        key = cand_eid or cand_name
        if key not in best_per_key or dist < best_per_key[key][0]:
            best_per_key[key] = (dist, cand_name, cand_eid)

    return sorted(best_per_key.items(), key=lambda kv: kv[1][0])


def _has_location_checkin(scan_id, employee_id, verified_at):
    """
    Parehong batayan ito ng _is_fully_verified() sa api_controller.py:
    isang scan ay itinuturing na kumpleto/fully verified (para sa Monitoring
    AT Analytics) lamang kapag may matching row sa location_checkins —
    alinman sa pamamagitan ng scan_id, o ng employee_id + time window
    paikot sa verified_at. Kung wala, "hindi kumpleto" ang scan at hindi
    dapat magpakita kahit saan.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()

        if scan_id:
            cur.execute(
                "SELECT 1 FROM location_checkins WHERE scan_id = %s LIMIT 1",
                (str(scan_id),)
            )
            if cur.fetchone():
                return True

        if not verified_at:
            return False

        verified_at_utc = (
            verified_at if verified_at.tzinfo is None
            else verified_at.astimezone(timezone.utc).replace(tzinfo=None)
        )
        window_end = verified_at_utc + timedelta(minutes=LOCATION_MATCH_WINDOW_MINUTES)

        cur.execute(
            """
            SELECT 1 FROM location_checkins
            WHERE employee_id = %s AND created_at BETWEEN %s AND %s
            LIMIT 1
            """,
            (employee_id, verified_at_utc, window_end)
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


class DeepFaceRecogController:
    """liveness ng face recog attendance"""

    @staticmethod
    def scan_face(data):
        try:
            def decode_frame(data_url):
                raw = data_url.split(",")[1]
                img_bytes = base64.b64decode(raw)
                np_arr = np.frombuffer(img_bytes, np.uint8)
                return cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            baseline_frame = decode_frame(data["baseline"])
            action_frame = decode_frame(data["action"])

            baseline_frame = enhance_low_light(baseline_frame)
            action_frame = enhance_low_light(action_frame)

            frame = action_frame

            expected_employee_id = data.get("expected_employee_id")

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            face_locations = face_recognition.face_locations(
                rgb_frame, model="hog", number_of_times_to_upsample=2
            )

            if len(face_locations) == 0:
                print("Face detection missed on first pass, retrying with more upsampling...")
                face_locations = face_recognition.face_locations(
                    rgb_frame, model="hog", number_of_times_to_upsample=3
                )

            face_encodings = face_recognition.face_encodings(
                rgb_frame,
                face_locations,
                num_jitters=SCAN_NUM_JITTERS,
                model=ENCODING_MODEL
            )

            if len(face_encodings) == 0:
                return {
                    "match": "No Face Detected",
                    "score": 0,
                    "scanned_image": None,
                    "registered_image": None
                }

            if len(known_face_encodings) == 0:
                return {
                    "match": "No Registered Faces",
                    "score": 0,
                    "scanned_image": None,
                    "registered_image": None
                }

            baseline_rgb = cv2.cvtColor(baseline_frame, cv2.COLOR_BGR2RGB)
            baseline_locations = face_recognition.face_locations(
                baseline_rgb, model="hog", number_of_times_to_upsample=2
            )
            if len(baseline_locations) == 0:
                baseline_locations = face_recognition.face_locations(
                    baseline_rgb, model="hog", number_of_times_to_upsample=3
                )

            live_check_passed = False
            mouth_ratio = None

            if len(baseline_locations) > 0:
                baseline_landmarks_list = face_recognition.face_landmarks(
                    baseline_rgb, [baseline_locations[0]]
                )
                action_landmarks_list = face_recognition.face_landmarks(
                    rgb_frame, [face_locations[0]]
                )

                if baseline_landmarks_list and action_landmarks_list:
                    baseline_eye_dist = eye_distance(baseline_landmarks_list[0])
                    action_eye_dist = eye_distance(action_landmarks_list[0])

                    if baseline_eye_dist > 0 and action_eye_dist > 0:
                        baseline_mouth = mouth_width(baseline_landmarks_list[0]) / baseline_eye_dist
                        action_mouth = mouth_width(action_landmarks_list[0]) / action_eye_dist

                        if baseline_mouth > 0:
                            mouth_ratio = action_mouth / baseline_mouth

                live_check_passed = mouth_ratio is not None and mouth_ratio >= SMILE_GROWTH_THRESHOLD

                print(f"Liveness check | mouth_ratio (eye-normalized): "
                      f"{'-' if mouth_ratio is None else round(mouth_ratio, 3)} "
                      f"(need >= {SMILE_GROWTH_THRESHOLD}) "
                      f"| passed: {live_check_passed}")
            else:
                print("No face found in baseline frame - cannot run liveness check.")

            if not live_check_passed:
                return {
                    "match": "Not Qualified",
                    "score": 0,
                    "scanned_image": None,
                    "registered_image": None
                }

            face_encoding = face_encodings[0]

            ranked = best_match_per_employee(face_encoding)

            print("Top candidates for this scan:")
            for key, (dist, nm, eid) in ranked[:3]:
                print(f"  {nm} (employee_id={eid}): distance={dist:.4f}")

            name = "Unknown"
            score = 0
            matched_employee_id = None
            matched_key = None
            qr_mismatch = False
            employee_lookup_note = None

            mismatch_identified_key = None
            mismatch_identified_name = None
            mismatch_identified_employee_id = None

            if expected_employee_id:
                own_idxs = [
                    i for i, eid in enumerate(known_face_employee_ids)
                    if eid == expected_employee_id
                ]

                own_distance = None
                if own_idxs:
                    own_distance = min(
                        face_recognition.face_distance(
                            [known_face_encodings[i] for i in own_idxs],
                            face_encoding
                        )
                    )

                qr_owner_employees = load_employees()
                qr_owner_info = qr_owner_employees.get(expected_employee_id)
                qr_owner_name = (
                    qr_owner_info.get("name") if qr_owner_info
                    else (known_face_names[own_idxs[0]] if own_idxs else expected_employee_id)
                )

                name = qr_owner_name
                matched_employee_id = expected_employee_id
                matched_key = expected_employee_id
                score = round(max(0, (1 - own_distance)) * 100, 2) if own_distance is not None else 0

                if own_distance is not None and own_distance <= TOLERANCE:
                    qr_mismatch = False
                    print(f"QR-matched employee verified: {name} "
                          f"(distance={own_distance:.4f}, score={score}%)")
                elif own_distance is None:
                    # This employee has no usable registered face encoding at
                    # all (never registered, or their registration photo
                    # failed encoding e.g. multiple faces / no face found).
                    # There is nothing to compare the scan against for THIS
                    # employee, so we must not report it as "looks like
                    # someone else" - that conflates two different problems.
                    qr_mismatch = True
                    print(f"QR MISMATCH (attendance blocked): expected={expected_employee_id} "
                          f"name={name} - no registered face on file for this employee_id.")

                    employee_lookup_note = (
                        f"Access denied: '{name}' (Employee ID: {expected_employee_id}) "
                        f"has no registered face on file, so the scan could not be "
                        f"compared. Please register this employee's face via "
                        f"registerface.html. Attendance was NOT recorded."
                    )
                else:
                    qr_mismatch = True

                    best_key, (best_distance, best_name, best_eid) = ranked[0]
                    is_confident_other = best_distance <= TOLERANCE
                    belongs_to_someone_else = is_confident_other and (best_eid or best_key) != expected_employee_id

                    if belongs_to_someone_else:
                        # Ang best_name dito ay galing sa filename parsing
                        # (person_name mula sa load_known_faces) - HINDI ito
                        # ang tunay na pangalan ng empleyado. Halimbawa,
                        # dahil ang filename ay "{employee_id}_{random8hex}.jpg",
                        # ang best_name ay maaaring lumabas na "594eec33" -
                        # isang random hex suffix, hindi pangalan ng tao.
                        # Kaya kailangang i-lookup ang TUNAY na pangalan mula
                        # sa Postgres employees.name column gamit ang
                        # employee_id, at 'yon ang ipapakita sa user - hindi
                        # ang naka-parse na piraso ng filename.
                        real_employee_records = load_employees()
                        real_info = real_employee_records.get(best_eid) if best_eid else None
                        display_best_name = real_info.get("name") if real_info else best_name

                        mismatch_identified_key = best_key
                        mismatch_identified_name = display_best_name
                        mismatch_identified_employee_id = best_eid
                        employee_lookup_note = (
                            f"Access denied: match score ({score}%) is below the "
                            f"required {MIN_ACCESS_SCORE_PERCENT}% threshold for "
                            f"'{name}' (Employee ID: {expected_employee_id}). The "
                            f"scanned face actually belongs to registered employee "
                            f"'{display_best_name}' (Employee ID: {best_eid}). Attendance "
                            f"was NOT recorded."
                        )
                    else:
                        employee_lookup_note = (
                            f"Access denied: match score ({score}%) for "
                            f"'{name}' (Employee ID: {expected_employee_id}) is "
                            f"below the required {MIN_ACCESS_SCORE_PERCENT}% "
                            f"threshold. Attendance was NOT recorded."
                        )

                    print(f"QR MISMATCH (attendance blocked): expected={expected_employee_id} "
                          f"name={name} score={score}% "
                          f"own_distance={'-' if own_distance is None else round(own_distance, 4)} "
                          f"closest_overall={best_name} ({best_distance:.4f})")
            else:
                best_key, (best_distance, best_name, best_employee_id) = ranked[0]
                second_distance = ranked[1][1][0] if len(ranked) > 1 else None

                is_confident_match = best_distance <= TOLERANCE
                is_unambiguous = (
                    second_distance is None
                    or (second_distance - best_distance) >= MIN_MARGIN
                )

                if is_confident_match and is_unambiguous:
                    # Parehong dahilan: i-lookup ang TUNAY na pangalan mula sa
                    # Postgres gamit ang employee_id, sa halip na gamitin ang
                    # best_name na galing sa filename parsing (posibleng
                    # random hex suffix lang, hindi tunay na pangalan).
                    real_employee_records = load_employees()
                    real_info = real_employee_records.get(best_employee_id) if best_employee_id else None
                    name = real_info.get("name") if real_info else best_name
                    score = round(max(0, (1 - best_distance)) * 100, 2)
                    matched_employee_id = best_employee_id
                    matched_key = best_key
                else:
                    name = "Unknown"

                print(f"Global match: {name} (key={matched_key}) | "
                      f"Best distance: {best_distance:.4f} | "
                      f"Runner-up gap: "
                      f"{'-' if second_distance is None else round(second_distance - best_distance, 4)}")

            _, buffer = cv2.imencode(".jpg", frame)
            scanned_b64 = "data:image/jpeg;base64," + base64.b64encode(buffer).decode("utf-8")

            if matched_key:
                registered_b64 = known_face_images.get(matched_key)
            elif mismatch_identified_key:
                registered_b64 = known_face_images.get(mismatch_identified_key)
            else:
                registered_b64 = None

            employee_id = None
            contact = None
            address = None
            date_hired = None
            qr_code_image = None
            verified_at = None

            if expected_employee_id:
                employees = load_employees()
                employee_info = employees.get(expected_employee_id)

                if employee_info:
                    employee_id = expected_employee_id
                    date_hired = employee_info.get("date_hired")

                    if not qr_mismatch:
                        contact = employee_info.get("contact")
                        address = employee_info.get("address")
                        qr_code_image = get_qr_code_data_url(employee_id)
                else:
                    employee_lookup_note = employee_lookup_note or (
                        f"QR code refers to employee_id '{expected_employee_id}', "
                        f"which has no matching record in PostgreSQL."
                    )

            elif name != "Unknown" and not qr_mismatch:
                employees = load_employees()
                employee_info = None

                if matched_employee_id and matched_employee_id in employees:
                    employee_id = matched_employee_id
                    employee_info = employees[employee_id]
                else:
                    employee_id, employee_info = find_employee_by_name(name)

                if employee_info:
                    contact = employee_info.get("contact")
                    address = employee_info.get("address")
                    date_hired = employee_info.get("date_hired")
                    qr_code_image = get_qr_code_data_url(employee_id)
                else:
                    employee_lookup_note = (
                        f"Face matched as '{name}', but no PostgreSQL record has "
                        f"that exact name. Check for typos/spacing, or that "
                        f"this person was registered via registerface.html."
                    )

            return {
                "match": name,
                "score": score,
                "scanned_image": scanned_b64,
                "registered_image": registered_b64,
                "employee_id": employee_id,
                "contact": contact,
                "address": address,
                "date_hired": date_hired,
                "qr_code_image": qr_code_image,
                "employee_lookup_note": employee_lookup_note,
                "qr_mismatch": qr_mismatch,
                "access_denied": qr_mismatch,
                "mismatch_identified_name": mismatch_identified_name,
                "mismatch_identified_employee_id": mismatch_identified_employee_id,
                "verified_at": verified_at.strftime("%Y-%m-%d %H:%M:%S") if verified_at else None
            }

        except Exception as e:
            print("ERROR:", e)
            return {
                "match": "Error",
                "score": 0,
                "scanned_image": None,
                "registered_image": None
            }

    @staticmethod
    def log_attendance(employee_id, name, score, scanned_b64, registered_b64, scan_type, qr_code_image):
        verified_at, scan_id = log_verified_scan(
            employee_id, name, score, scanned_b64, registered_b64,
            scan_type=scan_type, qr_code_image=qr_code_image,
            status="pending_location",
            note="Naghihintay pa sa location check-in bago maituring na kumpleto."
        )

        return {
            "success": True,
            "employee_id": employee_id,
            "name": name,
            "scan_type": scan_type,
            "verified_at": verified_at.strftime("%Y-%m-%d %H:%M:%S"),
            "scan_id": scan_id
        }

    @staticmethod
    def get_scan_logs():
        """
        Ipinapakita lang dito ang mga scan na FULLY VERIFIED — parehong
        pumasa sa face-match score threshold AT may matching row sa
        location_checkins (via _has_location_checkin, kaparehong
        batayan ng _is_fully_verified() sa Analytics). Ito ang gumawa
        dating hindi nag-uupdate ang stale na "status" column kaya
        laging blangko ang Monitoring kahit may fully verified na sa
        Analytics — ngayon parehong batayan na ang ginagamit ng dalawa.
        """
        logs = []
        cursor = fetch_recent_scans(200)
        print(f"[get_scan_logs] fetch_recent_scans returned {len(cursor)} row(s)")

        for doc in cursor:
            score = doc.get("score", 0) or 0
            scan_id = doc.get("id")
            employee_id = doc.get("employee_id")
            verified_at = doc.get("verified_at")

            # Kailangan pumasa muna sa face-match threshold.
            if score < MIN_ACCESS_SCORE_PERCENT:
                print(f"[get_scan_logs] SKIP scan_id={scan_id} employee_id={employee_id} "
                      f"score={score} < MIN_ACCESS_SCORE_PERCENT={MIN_ACCESS_SCORE_PERCENT}")
                continue

            # Kailangan may location check-in bago ituring na kumpleto.
            has_checkin = _has_location_checkin(scan_id, employee_id, verified_at)
            print(f"[get_scan_logs] scan_id={scan_id} employee_id={employee_id} "
                  f"score={score} verified_at={verified_at} has_location_checkin={has_checkin}")
            if not has_checkin:
                continue

            scanned_bytes = doc.get("scanned_image_data")
            registered_bytes = doc.get("registered_image_data")

            if scanned_bytes:
                photo = "data:image/jpeg;base64," + base64.b64encode(bytes(scanned_bytes)).decode("utf-8")
            elif registered_bytes:
                photo = "data:image/jpeg;base64," + base64.b64encode(bytes(registered_bytes)).decode("utf-8")
            else:
                photo = None

            if hasattr(verified_at, "isoformat"):
                verified_at_utc = (
                    verified_at if verified_at.tzinfo is not None
                    else verified_at.replace(tzinfo=timezone.utc)
                )
                timestamp = verified_at_utc.isoformat().replace("+00:00", "Z")
            else:
                timestamp = str(verified_at or "")

            logs.append({
                "id": str(doc.get("id")),
                "name": doc.get("name", "Unknown"),
                "qrId": employee_id or "",
                "photo": photo,
                "qrCodeImage": get_qr_code_data_url(employee_id),
                "matchPercentage": round(score, 1),
                "status": "verified",
                "note": doc.get("note"),
                "timestamp": timestamp,
                "scanType": doc.get("scan_type"),
            })

        return logs