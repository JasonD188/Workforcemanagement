

import threading
import time

from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id
from appwrite.id import ID
from appwrite.input_file import InputFile


def _photo_exists_in_appwrite(file_id):
    """True kung buo pa ang file sa Appwrite Storage, False kung tinanggal na
    (o hindi na ma-access)."""
    if not file_id:
        return False
    try:
        storage.get_file(bucket_id=bucket_id, file_id=file_id)
        return True
    except Exception:
        return False


def _restore_one_employee_photo(employee_id, old_file_id, photo_bytes):
   
    try:
        result = storage.create_file(
            bucket_id=bucket_id,
            file_id=ID.unique(),
            file=InputFile.from_bytes(photo_bytes, filename=f"{employee_id}_restored.jpg")
        )
        new_file_id = result.id
    except Exception as e:
        print(f"[appwrite_backup] Hindi na-restore ang photo ni {employee_id}: {e}")
        return False

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE employees SET photo_file_id = %s WHERE employee_id = %s",
            (new_file_id, employee_id)
        )
        conn.commit()
    finally:
        conn.close()

    print(f"[appwrite_backup] Na-restore ang photo ni {employee_id} "
          f"(dating file_id: {old_file_id} -> bago: {new_file_id})")
    return True


def restore_missing_photos():
   
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT employee_id, photo_file_id, photo_data FROM employees "
            "WHERE photo_data IS NOT NULL AND photo_deleted_by_admin = false"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    restored_count = 0
    for employee_id, photo_file_id, photo_data in rows:
        if _photo_exists_in_appwrite(photo_file_id):
            continue  # buo pa, walang gagawin

        # Nawawala na sa Appwrite pero may backup tayo sa Postgres - i-restore.
        photo_bytes = bytes(photo_data) if photo_data else None
        if not photo_bytes:
            continue

        if _restore_one_employee_photo(employee_id, photo_file_id, photo_bytes):
            restored_count += 1

    if restored_count:
        print(f"[appwrite_backup] Tapos na ang check - {restored_count} litrato ang na-restore.")
    return restored_count


def start_auto_restore_scheduler(interval_minutes=10):
   

    def _loop():
        while True:
            try:
                restore_missing_photos()
            except Exception as e:
                print(f"[appwrite_backup] Error habang nag-che-check ng photos: {e}")
            time.sleep(interval_minutes * 60)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print(f"[appwrite_backup] Auto-restore scheduler started (kada {interval_minutes} minuto).")