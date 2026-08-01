import os

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from api import api_bp
from registerface import registerface_bp
from deepfacerecog import deepfacerecog_bp
from qrscanner import qrcodescanner_bp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "temporary-dev-key-change-in-production")

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CORS(app, resources={r"/*": {"origins": "*"}})

app.register_blueprint(registerface_bp, url_prefix="/registerface")
app.register_blueprint(api_bp)

app.register_blueprint(
    deepfacerecog_bp,
    url_prefix="/deepfacerecog"
)

app.register_blueprint(
    qrcodescanner_bp,
    url_prefix="/qrcodescanner"
)


@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/loginuser")
def loginuser():
    return render_template("loginuser.html")

@app.route("/user_dashboard")
def user_dashboard():
    return render_template("user_dashboard.html")

@app.route("/uiface")
def deepfacerecog():
    return render_template("uiface.html")

@app.route("/qrcodescanner")
def qrcodescanner():
    return render_template("qrcodescanner.html")

@app.route("/userregister")
def userregister():
    return render_template("userregister.html")

print(app.url_map)

if __name__ == "__main__":
    app.run(debug=False)