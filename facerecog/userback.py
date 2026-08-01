#API user
import os
from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix


from deepfacerecog import deepfacerecog_bp
from qrscanner import qrcodescanner_bp
import api

app = Flask(__name__)


app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-change-me")

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_proto=1,
    x_host=1
)

app.register_blueprint(
    deepfacerecog_bp,
    url_prefix="/deepfacerecog"
)

app.register_blueprint(qrcodescanner_bp, url_prefix="/qrcodescanner")


app.register_blueprint(api.api_bp)


@app.route("/")
def login_page():
    return render_template("loginuser.html")  # Sign In page


@app.route("/register")
def register_page():
    return render_template("userregister.html")  # Create Account page


@app.route("/dashboard")
def user_dashboard():
    return render_template("user_dashboard.html")


print(app.url_map)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)