# auth/utils.py
import base64
from config.appwrite_config import storage, bucket_id


def clean_str(value):
    return str(value or "").strip()


def get_employee_photo_base64(photo_file_id):
    """Pull the employee's registered photo from Appwrite Storage using
    the file id stored on the employee record, and return it as a base64
    data URL, or None if there isn't one on file."""
    if not photo_file_id:
        return None
    try:
        file_bytes = storage.get_file_download(bucket_id=bucket_id, file_id=photo_file_id)
    except Exception as e:
        print(f"Could not load photo from Appwrite Storage ({photo_file_id}):", e)
        return None
    encoded = base64.b64encode(file_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def public_employee(doc):
    """I-strip ang sensitive fields bago i-return sa frontend."""
    if not doc:
        return None
    return {
        "employee_id": doc.get("employee_id"),
        "name": doc.get("name"),
        "email": doc.get("email"),
        "role": doc.get("role", "employee"),
        "photo_url": get_employee_photo_base64(doc.get("photo_file_id")),
    }