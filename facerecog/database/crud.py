from .postgress import get_connection



EMPLOYEE_LIST_COLUMNS = [
    "employee_id",
    "name",
    "contact",
    "address",
    "date_hired",
    "qr_path",
    "qr_code_value",
    "photo_file_id",
    "is_active",
    "face_registered",
    "created_at",
    "updated_at",
]


def _row_to_employee_dict(row, columns):
  
    record = dict(zip(columns, row))
    for key, value in record.items():
        if hasattr(value, "isoformat"):
            record[key] = value.isoformat()
    return record


def get_all_employees():
 
    conn = get_connection()
    try:
        cur = conn.cursor()
        columns_sql = ", ".join(EMPLOYEE_LIST_COLUMNS)
        cur.execute(f"SELECT {columns_sql} FROM employees ORDER BY created_at DESC;")
        rows = cur.fetchall()
        cur.close()
        return [_row_to_employee_dict(row, EMPLOYEE_LIST_COLUMNS) for row in rows]
    finally:
        conn.close()


def get_employee_by_id(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM employees WHERE employee_id = %s;", (employee_id,))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()


def create_employee(employee_id, name, role="employee"):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO employees (employee_id, name, role) VALUES (%s, %s, %s) RETURNING id;",
            (employee_id, name, role)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return new_id
    finally:
        conn.close()


def update_employee(employee_id, name=None, role=None, is_active=None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE employees
            SET name = COALESCE(%s, name),
                role = COALESCE(%s, role),
                is_active = COALESCE(%s, is_active),
                updated_at = now()
            WHERE employee_id = %s;
            """,
            (name, role, is_active, employee_id)
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()


def delete_employee(employee_id):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM employees WHERE employee_id = %s;", (employee_id,))
        conn.commit()
        cur.close()
    finally:
        conn.close()