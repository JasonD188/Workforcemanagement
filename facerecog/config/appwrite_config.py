import os
from dotenv import load_dotenv
from appwrite.client import Client
from appwrite.services.storage import Storage

CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))   
BASE_DIR = os.path.dirname(CONFIG_DIR)                    
env_path = os.path.join(BASE_DIR, "env", ".env")           

print("Looking for .env at:", env_path)
print(".env exists?:", os.path.exists(env_path))

load_dotenv(env_path)

APPWRITE_ENDPOINT = os.environ.get("APPWRITE_ENDPOINT")
APPWRITE_PROJECT_ID = os.environ.get("APPWRITE_PROJECT_ID")
APPWRITE_API_KEY = os.environ.get("APPWRITE_API_KEY")
APPWRITE_BUCKET_ID = os.environ.get("APPWRITE_BUCKET_ID")

print("ENDPOINT:", repr(APPWRITE_ENDPOINT))
print("PROJECT_ID:", repr(APPWRITE_PROJECT_ID))
print("API_KEY:", repr(APPWRITE_API_KEY))
print("BUCKET_ID:", repr(APPWRITE_BUCKET_ID))

if not all([APPWRITE_ENDPOINT, APPWRITE_PROJECT_ID, APPWRITE_API_KEY, APPWRITE_BUCKET_ID]):
    raise RuntimeError("Missing Appwrite config in .env")

client = Client()
client.set_endpoint(APPWRITE_ENDPOINT)
client.set_project(APPWRITE_PROJECT_ID)
client.set_key(APPWRITE_API_KEY)

storage = Storage(client)
bucket_id = APPWRITE_BUCKET_ID