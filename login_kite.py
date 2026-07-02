import json
from kiteconnect import KiteConnect
from flask import Flask, request, session, redirect
from dotenv import load_dotenv
import os
import traceback
import subprocess
import sys

RISK_MANAGER_PID_FILE = "risk_manager.pid"


def load_credentials():
    json_file = os.path.join(current_file_path, "credentials.json")
    with open(json_file) as f:
        return json.load(f)


def get_client_doc_from_json(client_id):
    try:
        return load_credentials()[client_id]
    except Exception:
        traceback.print_exc()

def save_access_token(
    client_id,
    access_token
):

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


def all_clients_have_access_tokens():
    credentials = load_credentials()
    return all(
        bool(client_doc.get("access_token"))
        for client_doc in credentials.values()
    )


def is_process_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_risk_manager_if_needed():
    pid_file = os.path.join(
        current_file_path,
        RISK_MANAGER_PID_FILE
    )

    if os.path.exists(pid_file):
        with open(pid_file) as f:
            pid_text = f.read().strip()

        if pid_text and is_process_running(int(pid_text)):
            return int(pid_text), False

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
    credentials = load_credentials()
    client_links = "\n".join(
        f"""
        <a href="/login/{client_id}" class="client-row">
            <span>{client_id}</span>
            <strong>Login</strong>
        </a>
        """
        for client_id in credentials.keys()
    )

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

            .client-row {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 14px 16px;
                border-radius: 6px;
                color: #1f2933;
                text-decoration: none;
            }}

            .client-row:hover {{
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
                <p>Select each client once to refresh its Kite access token. The risk engine starts for all configured clients after every client has an access token.</p>
            </section>
            <section class="list">
                {client_links}
            </section>
        </main>
    </body>
    </html>
    """


@app.route("/login/<client_id>")
def login(client_id):
    user_doc = get_client_doc_from_json(client_id)
    api_key = user_doc["api_key"]

    kite = KiteConnect(api_key=api_key)
    session["client_id"] = client_id

    return redirect(kite.login_url())


@app.route("/callback")
def callback():
    request_token = request.args.get("request_token")
    client_id = session.get("client_id")

    if not client_id:
        return "Missing client session. Start again from /.", 400

    user_doc = get_client_doc_from_json(client_id)
    kite = KiteConnect(api_key=user_doc["api_key"])
    api_secret = user_doc["secret_key"]
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]
    save_access_token(
        client_id,
        access_token
    )

    if not all_clients_have_access_tokens():
        return (
            f"Login successful for {client_id}. "
            "Refresh the remaining client access tokens from / before starting the risk manager."
        )

    pid, started = start_risk_manager_if_needed()
    status = "started" if started else "already running"

    return (
        f"Login successful for {client_id}. "
        f"Risk manager {status} for all configured clients. pid={pid}"
    )



if __name__=="__main__":
    app.run(host = "0.0.0.0",port=3000)
