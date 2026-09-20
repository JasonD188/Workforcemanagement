import os
from datetime import timedelta
from dotenv import load_dotenv

from flask import Flask, jsonify, render_template, request, session
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_wtf.csrf import CSRFProtect, generate_csrf
from api_routes import api_bp
from apiuser_routes import apiuser_bp
from auth.routeslogin import auth_bp
from registerface_routes import registerface_bp
from deepfacerecog_routes import deepfacerecog_bp
from qrscanner_routes import qrcodescanner_bp
from database.postgress import get_connection
from config.appwrite_config import storage, bucket_id
from auth.admin_routes import admin_bp
from history_routes import employee_bp
from api_analytics.analytics import analytics_export_bp
from otp_service.otp_auth import otp_bp
from auth.decorators import login_required, role_required, page_login_required, page_role_required


env_path = os.path.join(os.path.dirname(__file__), "env", ".env")
load_dotenv(env_path)

app = Flask(__name__)

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is not set.")
app.secret_key = secret_key

csrf = CSRFProtect(app)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "").split(",")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}}, supports_credentials=True)

# Nililinis ang mga space at walang laman na entry
ALLOWED_ADMIN_IPS = [
    ip.strip()
    for ip in os.environ.get("ALLOWED_ADMIN_IPS", "").split(",")
    if ip.strip()
]


def get_client_ip():
    """
    Kunin ang totoong IP ng bisita.
    Sa likod ng Cloudflare (tunnel man o proxy), nasa CF-Connecting-IP ito.
    """
    ip = (
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For", "").split(",")[0]
        or request.remote_addr
        or ""
    )
    return ip.strip()


@app.before_request
def restrict_admin_login():
    if request.path == "/loginadmin" and ALLOWED_ADMIN_IPS:
        detected_ip = get_client_ip()

        # ---- DEBUG (tanggalin na pagkatapos ma-verify) ----
        print(f"[DEBUG] CF-Connecting-IP: '{request.headers.get('CF-Connecting-IP')}'")
        print(f"[DEBUG] X-Forwarded-For: '{request.headers.get('X-Forwarded-For')}'")
        print(f"[DEBUG] remote_addr: '{request.remote_addr}'")
        print(f"[DEBUG] Detected IP (final): '{detected_ip}'")
        print(f"[DEBUG] Allowed IPs list: {ALLOWED_ADMIN_IPS}")
        print(f"[DEBUG] Match found: {detected_ip in ALLOWED_ADMIN_IPS}")
        # ---- END DEBUG ----

        if detected_ip not in ALLOWED_ADMIN_IPS:
            return jsonify({"error": "Not found."}), 404


app.register_blueprint(auth_bp)
app.register_blueprint(registerface_bp, url_prefix="/registerface")
app.register_blueprint(api_bp)
app.register_blueprint(apiuser_bp)
app.register_blueprint(admin_bp)

app.register_blueprint(employee_bp)
app.register_blueprint(analytics_export_bp)

app.register_blueprint(
    deepfacerecog_bp,
    url_prefix="/deepfacerecog"
)

app.register_blueprint(
    qrcodescanner_bp,
    url_prefix="/qrcodescanner"
)


@app.route("/api/csrf-token", methods=["GET"])
def get_csrf_token():
    return jsonify({"csrf_token": generate_csrf()})


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, private'
    response.headers['Pragma'] = 'no-cache'
    return response


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/")
@page_role_required('admin')
def dashboard():
    return render_template("dashboard.html")


@app.route("/loginuser")
def loginuser():
    return render_template("loginuser.html")


@app.route("/user_dashboard")
@page_login_required
def user_dashboard():
    return render_template("user_dashboard.html")


@app.route("/uiface")
@page_role_required('admin')
def deepfacerecog():
    return render_template("uiface.html")


@app.route("/qrcodescanner")
@role_required('admin', 'kiosk')
def qrcodescanner():
    return render_template("qrcodescanner.html")


@app.route("/userregister")
def userregister():
    return render_template("userregister.html")


try:
    pg_conn = get_connection()
    print("PostgreSQL Connected!")
    pg_conn.close()
except Exception as e:
    print(f"PostgreSQL connection failed: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting on port {port}")
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )