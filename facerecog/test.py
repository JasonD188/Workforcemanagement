import os

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from api import api_bp
from registerface import registerface_bp
from deepfacerecog import deepfacerecog_bp

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "temporary-dev-key-change-in-production")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

CORS(app, resources={r"/*": {"origins": "*"}})




app.register_blueprint(registerface_bp, url_prefix="/registerface")
app.register_blueprint(api_bp)

# ITO ANG KULANG
app.register_blueprint(
    deepfacerecog_bp,
    url_prefix="/deepfacerecog"
)


@app.route("/")
def dashboard():
    return render_template("dashboard.html")

@app.route("/loginuser")
def loginuser():
    return render_template("loginuser.html")

print(app.url_map)

if __name__ == "__main__":
    app.run(debug=False)