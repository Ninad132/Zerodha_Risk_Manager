import json
from kiteconnect import KiteConnect
from flask import Flask, request, redirect
from dotenv import load_dotenv
import os
import traceback
import subprocess
import sys
import fcntl
import get_kite_client

RISK_MANAGER_PID_FILE = "risk_manager.pid"


def get_client_id():
    return get_kite_client.get_single_client_id()


def get_client_doc_from_json():
    try:
        return get_kite_client.get_client_doc_from_json()
    except Exception:
        traceback.print_exc()

def save_access_token(
    access_token
):
    client_id = get_client_id()

    json_file = os.path.join(
        current_file_path,
        "credentials.json"
    )

    with open(json_file) as f:
        data = json.load(f)

    data[client_id]["access_token"] = access_token

    with open(json_file, "w") as f:
        json.dump(
            data,
            f,
            indent=4
        )


def is_process_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_running_pid(pid_file):
    if not os.path.exists(pid_file):
        return None

    with open(pid_file) as f:
        pid_text = f.read().strip()

    if not pid_text:
        return None

    try:
        pid = int(pid_text)
    except ValueError:
        return None

    if is_process_running(pid):
        return pid

    return None


def start_risk_manager_if_needed():
    pid_file = os.path.join(
        current_file_path,
        RISK_MANAGER_PID_FILE
    )
    lock_file = f"{pid_file}.lock"

    with open(lock_file, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        running_pid = read_running_pid(pid_file)
        if running_pid:
            return running_pid, False

        risk_manager_path = os.path.join(
            current_file_path,
            "positions_kill_switch.py"
        )

        log_file = open(
            os.path.join(current_file_path, "risk_manager.log"),
            "a"
        )

        process = subprocess.Popen(
            [sys.executable, risk_manager_path],
            stdout=log_file,
            stderr=log_file,
            start_new_session=True
        )

        with open(pid_file, "w") as f:
            f.write(str(process.pid))

        return process.pid, True


current_file_path = os.path.dirname(os.path.realpath(__file__))
load_dotenv()
app=Flask(__name__)
app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "kite-risk-manager"
)

@app.route("/")
def index():
    client_id = get_client_id()

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Kite Login</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                font-family: Arial, sans-serif;
            }}

            body {{
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                background: #f4f6f8;
                color: #1f2933;
            }}

            .panel {{
                width: min(460px, calc(100vw - 32px));
                background: white;
                border: 1px solid #d9e2ec;
                border-radius: 8px;
                box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
            }}

            .header {{
                padding: 24px;
                border-bottom: 1px solid #e5eaf0;
            }}

            h1 {{
                font-size: 22px;
                font-weight: 700;
                margin-bottom: 8px;
            }}

            p {{
                color: #52606d;
                font-size: 14px;
                line-height: 1.4;
            }}

            .list {{
                padding: 8px;
            }}

            .login-link {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 14px 16px;
                border-radius: 6px;
                color: #1f2933;
                text-decoration: none;
            }}

            .login-link:hover {{
                background: #f0f4f8;
            }}

            strong {{
                color: #1d4ed8;
                font-size: 14px;
            }}
        </style>
    </head>
    <body>
        <main class="panel">
            <section class="header">
                <h1>Zerodha Risk Manager</h1>
                <p>Refresh the Kite access token for the configured account. The risk engine starts after login succeeds.</p>
            </section>
            <section class="list">
                <a href="/login" class="login-link">
                    <span>{client_id}</span>
                    <strong>Login</strong>
                </a>
            </section>
        </main>
    </body>
    </html>
    """


@app.route("/login")
def login():
    user_doc = get_client_doc_from_json()
    api_key = user_doc["api_key"]

    kite = KiteConnect(api_key=api_key)

    return redirect(kite.login_url())


@app.route("/callback")
def callback():
    request_token = request.args.get("request_token")
    client_id = get_client_id()

    user_doc = get_client_doc_from_json()
    kite = KiteConnect(api_key=user_doc["api_key"])
    api_secret = user_doc["secret_key"]
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]
    save_access_token(
        access_token
    )

    pid, started = start_risk_manager_if_needed()
    status = "started" if started else "already running"

    return (
        f"Login successful for {client_id}. "
        f"Risk manager {status}. pid={pid}"
    )



if __name__=="__main__":
    app.run(host = "0.0.0.0",port=3000)
