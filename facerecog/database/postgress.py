import psycopg2
import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), "..", "env", ".env")
load_dotenv(env_path)

def get_connection():
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD")
    )
    return conn
if __name__ == "__main__":
    conn = get_connection()
    print(" Connected successfully!")
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
    print(cur.fetchall())
    cur.close()
    conn.close()