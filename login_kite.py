import json
from kiteconnect import KiteConnect
from flask import Flask, request, redirect
from dotenv import load_dotenv
import os
import traceback
import subprocess
import sys
import fcntl
import signal
import get_kite_client

RISK_MANAGER_PID_FILE = "risk_manager.pid"
RISK_MANAGER_SCRIPT = "positions_kill_switch.py"


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


def get_process_status_and_command(pid):
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-o", "command=", "-p", str(pid)],
            check=False,
            capture_output=True,
            text=True
        )
    except OSError:
        return None, ""

    if result.returncode != 0:
        return None, ""

    output = result.stdout.strip()
    if not output:
        return None, ""

    parts = output.split(None, 1)
    status = parts[0]
    command = parts[1] if len(parts) > 1 else ""
    return status, command


def is_process_running(pid, expected_script=None):
    try:
        os.kill(pid, 0)
    except OSError:
        return False

    status, command = get_process_status_and_command(pid)
    if status is None:
        return False

    if "Z" in status:
        return False

    if expected_script and expected_script not in command:
        return False

    return True


def remove_stale_pid_file(pid_file):
    try:
        os.remove(pid_file)
    except FileNotFoundError:
        pass


def read_running_pid(pid_file, expected_script):
    if not os.path.exists(pid_file):
        return None

    with open(pid_file) as f:
        pid_text = f.read().strip()

    if not pid_text:
        remove_stale_pid_file(pid_file)
        return None

    try:
        pid = int(pid_text)
    except ValueError:
        remove_stale_pid_file(pid_file)
        return None

    if is_process_running(pid, expected_script):
        return pid

    remove_stale_pid_file(pid_file)
    return None


def start_risk_manager_if_needed():
    pid_file = os.path.join(
        current_file_path,
        RISK_MANAGER_PID_FILE
    )
    lock_file = f"{pid_file}.lock"

    with open(lock_file, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)

        risk_manager_path = os.path.join(
            current_file_path,
            RISK_MANAGER_SCRIPT
        )

        running_pid = read_running_pid(pid_file, risk_manager_path)
        if running_pid:
            return running_pid, False

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
signal.signal(signal.SIGCHLD, signal.SIG_IGN)
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
