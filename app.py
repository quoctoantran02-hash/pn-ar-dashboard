import json
import logging
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from flask import Flask, jsonify, Response, request, send_file
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

try:
    from waitress import serve
except ImportError:
    serve = None


ROOT_DIR = Path(__file__).resolve().parent
SPREADSHEET_ID = os.environ.get(
    "GOOGLE_SHEET_ID",
    "1a8F2emmY_54yRAIwnORYPJvhkxLgUj2Qh0gQJnEKCv4",
)
SHEET_RANGE = os.environ.get("GOOGLE_SHEET_RANGE", "raw_data!A2:M")
OAUTH_CREDENTIALS_PATH = Path(
    os.environ.get("GOOGLE_OAUTH_CREDENTIALS", ROOT_DIR / "credentials.json")
).expanduser()
OAUTH_TOKEN_PATH = Path(
    os.environ.get("GOOGLE_OAUTH_TOKEN", ROOT_DIR / "token.json")
).expanduser()
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
SHEETS_READ_SCOPES = {
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
}
AI_AGENT_URL = "https://smart.shopee.io/apis/smart/v1/orchestrator/deployments/invoke"
AI_AGENT_DEPLOYMENT_HASH_ID = os.environ.get(
    "AI_AGENT_DEPLOYMENT_HASH_ID",
    "kg0s8wvvabo4p158mw77qmgx",
)
AI_AGENT_ENDPOINT_KEY = os.environ.get("AI_AGENT_ENDPOINT_KEY", "")
AI_AGENT_USER_ID = os.environ.get("AI_AGENT_USER_ID", "")
HOST = os.environ.get("LOCAL_AI_HOST", "0.0.0.0")
PORT = int(os.environ.get("LOCAL_AI_PORT", "8765"))
MAX_AI_MESSAGE_LENGTH = 4000

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
FRONTEND_ORIGINS = {
    origin.strip().rstrip("/")
    for origin in os.environ.get("FRONTEND_ORIGINS", "").split(",")
    if origin.strip()
}


@app.after_request
def add_cors_headers(response):
    origin = request.headers.get("Origin")
    if request.path.startswith("/api/") and origin and origin.rstrip("/") in FRONTEND_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Headers"] = (
            request.headers.get("Access-Control-Request-Headers", "Content-Type")
        )
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Vary"] = "Origin"
    return response

_oauth_lock = threading.Lock()
_oauth_credentials = None

PARTIALS = {
    "<!-- include:Styles -->": "Styles.html",
    "<!-- include:Script_Filters -->": "Script_Filters.html",
    "<!-- include:Script_Charts -->": "Script_Charts.html",
    "<!-- include:Script_Table -->": "Script_Table.html",
    "<!-- include:Script_Main -->": "Script_Main.html",
}


class SheetDataError(Exception):
    """An actionable error while authorizing or reading the Google Sheet."""


def render_index():
    html = (ROOT_DIR / "index.html").read_text(encoding="utf-8")
    for marker, filename in PARTIALS.items():
        html = html.replace(
            marker,
            (ROOT_DIR / filename).read_text(encoding="utf-8"),
        )
    if "<!-- include:" in html:
        raise RuntimeError("index.html contains an unhandled partial include.")
    return html


def get_access_token():
    global _oauth_credentials

    with _oauth_lock:
        try:
            if _oauth_credentials is None:
                if OAUTH_TOKEN_PATH.is_file():
                    _oauth_credentials = Credentials.from_authorized_user_file(
                        str(OAUTH_TOKEN_PATH)
                    )
                else:
                    if not OAUTH_CREDENTIALS_PATH.is_file():
                        raise SheetDataError(
                            "Không tìm thấy credentials.json hoặc token.json. "
                            "Đặt OAuth client credentials vào thư mục project "
                            "hoặc cấu hình GOOGLE_OAUTH_CREDENTIALS."
                        )
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(OAUTH_CREDENTIALS_PATH),
                        SCOPES,
                    )
                    _oauth_credentials = flow.run_local_server(port=0)
                    OAUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
                    OAUTH_TOKEN_PATH.write_text(
                        _oauth_credentials.to_json(),
                        encoding="utf-8",
                    )

            granted_scopes = set(_oauth_credentials.scopes or [])
            if not granted_scopes.intersection(SHEETS_READ_SCOPES):
                raise SheetDataError(
                    "token.json chưa được cấp quyền đọc Google Sheets "
                    "(thiếu scope Google Sheets). Hãy xóa token.json "
                    "cũ rồi khởi động lại app để đăng nhập OAuth và cấp quyền "
                    "Google Sheets."
                )

            if _oauth_credentials.expired:
                if not _oauth_credentials.refresh_token:
                    raise SheetDataError(
                        "OAuth token đã hết hạn và không có refresh token. "
                        "Xóa token.json rồi khởi động lại để đăng nhập lại."
                    )
                _oauth_credentials.refresh(Request())
                OAUTH_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
                OAUTH_TOKEN_PATH.write_text(
                    _oauth_credentials.to_json(),
                    encoding="utf-8",
                )

            if not _oauth_credentials.valid:
                raise SheetDataError(
                    "OAuth token không hợp lệ. Xóa token.json rồi khởi động "
                    "lại để đăng nhập lại."
                )
            return _oauth_credentials.token
        except SheetDataError:
            raise
        except (OSError, ValueError, GoogleAuthError) as exc:
            raise SheetDataError(f"Không thể xác thực Google OAuth: {exc}") from exc


def _cell(row, index):
    return row[index] if index < len(row) else None


def _text(value):
    return "" if value is None else str(value).strip()


def _date_string(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (datetime(1899, 12, 30) + timedelta(days=value)).date().isoformat()
    return _text(value)[:10]


def _number(value):
    if value is None or value == "":
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def normalize_rows(rows):
    records = []
    for row in rows:
        task_name = _text(_cell(row, 1))
        if not task_name:
            continue
        records.append(
            {
                "taskId": task_name,
                "taskName": task_name,
                "type": "AR" if _text(_cell(row, 2)).upper() == "AR" else "PN",
                "title": _text(_cell(row, 3)),
                "content": _text(_cell(row, 4)),
                "product": _text(_cell(row, 5)),
                "pic": _text(_cell(row, 6)),
                "sent": _number(_cell(row, 7)),
                "impression": _number(_cell(row, 8)),
                "click": _number(_cell(row, 9)),
                "topic": _text(_cell(row, 10)) or "Chưa phân loại",
                "spam": _number(_cell(row, 11)),
                "sentDate": _date_string(_cell(row, 12)),
                "date": _date_string(_cell(row, 0)),
            }
        )
    return records


def fetch_dashboard_data():
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{SHEET_RANGE}"
    try:
        response = requests.get(
            url,
            params={"valueRenderOption": "UNFORMATTED_VALUE"},
            headers={"Authorization": f"Bearer {get_access_token()}"},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "không rõ"
        details = exc.response.text[:500] if exc.response is not None else str(exc)
        raise SheetDataError(
            f"Google Sheets API trả lỗi HTTP {status}: {details}"
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        raise SheetDataError(f"Không thể đọc dữ liệu Google Sheet: {exc}") from exc

    return normalize_rows(payload.get("values", []))


def _find_response_value(payload, keys):
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        for value in payload.values():
            result = _find_response_value(value, keys)
            if result:
                return result
    elif isinstance(payload, list):
        for value in payload:
            result = _find_response_value(value, keys)
            if result:
                return result
    return None


def extract_agent_reply(payload):
    reply = _find_response_value(
        payload,
        (
            "response_str",
            "output_str",
            "answer",
            "reply",
            "text",
            "content",
            "response",
            "output",
        ),
    )
    if reply:
        return reply
    return json.dumps(payload, ensure_ascii=False)


def invoke_ai_agent(message, thread_id):
    if not AI_AGENT_ENDPOINT_KEY or not AI_AGENT_USER_ID:
        raise ValueError(
            "AI chưa được cấu hình. Hãy đặt AI_AGENT_ENDPOINT_KEY và "
            "AI_AGENT_USER_ID rồi khởi động lại server."
        )

    payload = {
        "endpoint_deployment_hash_id": AI_AGENT_DEPLOYMENT_HASH_ID,
        "endpoint_deployment_key": AI_AGENT_ENDPOINT_KEY,
        "user_id": AI_AGENT_USER_ID,
        "message": {"input_str": message, "thread_id": thread_id},
        "mode": "sync",
        "streaming": False,
        "user_credentials": {"token_inputs": []},
        "tool_variables": {},
        "override_tool_variables": {},
        "auto_clean_up_session_files": False,
    }

    try:
        response = requests.post(AI_AGENT_URL, json=payload, timeout=120)
        response.raise_for_status()
        result = response.json()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "không rõ"
        details = exc.response.text[:500] if exc.response is not None else str(exc)
        app.logger.error("AI agent returned HTTP %s: %s", status, details)
        raise RuntimeError(f"Agent AI trả lỗi HTTP {status}.") from exc
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("AI agent request failed: %s", exc)
        raise RuntimeError("Không thể kết nối hoặc đọc phản hồi từ Agent AI.") from exc

    return {
        "reply": extract_agent_reply(result),
        "thread_id": _find_response_value(result, ("thread_id", "threadId"))
        or thread_id,
    }


@app.get("/")
def home():
    return Response(render_index(), mimetype="text/html")


@app.get("/dior.png")
def dior_avatar():
    return send_file(ROOT_DIR / "dior.png", mimetype="image/png")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/dashboard-data")
def dashboard_data():
    try:
        return jsonify(fetch_dashboard_data())
    except SheetDataError as exc:
        app.logger.error("Google Sheet data request failed: %s", exc)
        return jsonify({"error": str(exc)}), 502


@app.post("/api/ai-chat")
def ai_chat():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body phải là JSON object."}), 400

    message = body.get("message")
    thread_id = body.get("thread_id", "")
    if not isinstance(message, str) or not message.strip():
        return jsonify({"error": "Vui lòng nhập nội dung tin nhắn."}), 400
    if len(message) > MAX_AI_MESSAGE_LENGTH:
        return jsonify(
            {"error": f"Tin nhắn không được vượt quá {MAX_AI_MESSAGE_LENGTH} ký tự."}
        ), 400
    if not isinstance(thread_id, str):
        return jsonify({"error": "thread_id không hợp lệ."}), 400

    try:
        return jsonify(invoke_ai_agent(message.strip(), thread_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 503
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    app.logger.info("Starting dashboard on %s:%s", HOST, PORT)
    if serve is not None:
        serve(app, host=HOST, port=PORT)
    else:
        app.run(host=HOST, port=PORT, debug=False)
