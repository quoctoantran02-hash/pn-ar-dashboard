import base64
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

import requests
from flask import (
    Flask,
    jsonify,
    Response,
    request,
    redirect,
    send_file,
    session,
    stream_with_context,
)
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
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
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
]
SHEETS_READ_SCOPES = {
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
}
GOOGLE_DOCS_READ_SCOPES = {"https://www.googleapis.com/auth/documents.readonly"}
AI_AGENT_URL = "https://smart.shopee.io/apis/smart/v1/orchestrator/deployments/invoke"
AI_AGENT_FILES_URL = (
    "https://smart.shopee.io/apis/smart/v1/orchestrator/deployments/invoke_with_files"
)
AI_AGENT_CONTINUE_INTERRUPT_URL = (
    "https://smart.shopee.io/apis/smart/v1/orchestrator/deployments/"
    "continue_agent_v2_interrupt"
)
AI_AGENT_DEPLOYMENT_HASH_ID = os.environ.get(
    "AI_AGENT_DEPLOYMENT_HASH_ID",
    "57q27rzw5h8grj7dhm64pwzl", ## old haskey: kg0s8wvvabo4p158mw77qmgx
)
AI_AGENT_ENDPOINT_KEY = os.environ.get("AI_AGENT_ENDPOINT_KEY", "")
GOOGLE_OAUTH_WEB_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_WEB_CLIENT_ID", "")
SEATALK_APP_ID = os.environ.get("SEATALK_APP_ID", "Nzk2MjY4MzcwMDIz")
SEATALK_APP_SECRET = os.environ.get("SEATALK_APP_SECRET", "")
SEATALK_REDIRECT_URI = os.environ.get(
    "SEATALK_REDIRECT_URI", f"http://127.0.0.1:{os.environ.get('LOCAL_AI_PORT', '8765')}/"
)
SEATALK_TOKEN_URL = "https://openapi.seatalk.io/auth/app_access_token"
SEATALK_CODE_EXCHANGE_URL = "https://openapi.seatalk.io/open_login/code2employee"
SEATALK_EMPLOYEE_LOOKUP_URL = (
    "https://openapi.seatalk.io/contacts/v2/get_employee_code_with_email"
)
SEATALK_SINGLE_CHAT_URL = "https://openapi.seatalk.io/messaging/v2/single_chat"
SEATALK_INIT_STREAM_URL = (
    "https://openapi.seatalk.io/messaging/v2/single_chat/init_stream"
)
GOOGLE_DOCS_DOCUMENT_ID = "1Sn5pBv10g-xVSIaWpQzNZ6uyUfTxRhQhMUh2mqeIevM"
GOOGLE_DOCS_API_URL = (
    f"https://docs.googleapis.com/v1/documents/{GOOGLE_DOCS_DOCUMENT_ID}"
)
MAX_SEATALK_HTML_SIZE = 20 * 1024 * 1024
MAX_SEATALK_REPLY_LENGTH = 100_000
SESSION_SECRET_FILE = Path(
    os.environ.get("FLASK_SESSION_SECRET_FILE", ROOT_DIR / ".flask_session_secret")
).expanduser()
HOST = os.environ.get("LOCAL_AI_HOST", "0.0.0.0")
PORT = int(os.environ.get("LOCAL_AI_PORT", "8765"))
MAX_AI_MESSAGE_LENGTH = 4000


def session_cookie_secure_setting(setting, redirect_uri):
    if setting is None:
        return redirect_uri.lower().startswith("https://")
    return setting.lower() in {"1", "true", "yes"}


app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or ""
if not app.secret_key:
    try:
        app.secret_key = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        app.secret_key = secrets.token_urlsafe(48)
        SESSION_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with SESSION_SECRET_FILE.open("x", encoding="utf-8") as secret_file:
                secret_file.write(app.secret_key)
        except FileExistsError:
            app.secret_key = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
if not app.secret_key:
    raise RuntimeError("FLASK_SECRET_KEY or a persistent session secret is required.")

app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(hours=24),
    SESSION_REFRESH_EACH_REQUEST=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=session_cookie_secure_setting(
        os.environ.get("SESSION_COOKIE_SECURE"),
        SEATALK_REDIRECT_URI,
    ),
)
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
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

_oauth_lock = threading.Lock()
_oauth_credentials = None
_seatalk_token_lock = threading.Lock()
_seatalk_app_access_token = ""
_seatalk_token_expires_at = 0

PARTIALS = {
    "<!-- include:Styles -->": "Styles.html",
    "<!-- include:Script_Filters -->": "Script_Filters.html",
    "<!-- include:Script_Charts -->": "Script_Charts.html",
    "<!-- include:Script_Table -->": "Script_Table.html",
    "<!-- include:Script_Main -->": "Script_Main.html",
}


class SheetDataError(Exception):
    """An actionable error while authorizing or reading the Google Sheet."""


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = session.get("user")
        if not isinstance(user, dict) or not isinstance(user.get("email"), str):
            return jsonify({"error": "Vui lòng đăng nhập bằng Google hoặc SeaTalk."}), 401
        return view(*args, **kwargs)

    return wrapped


def frontend_config():
    return (
        "window.PNAR_API_BASE_URL = "
        + json.dumps(os.environ.get("FRONTEND_BACKEND_URL", "").rstrip("/"))
        + ";\nwindow.PNAR_GOOGLE_CLIENT_ID = "
        + json.dumps(GOOGLE_OAUTH_WEB_CLIENT_ID)
        + ";\nwindow.PNAR_SEATALK_APP_ID = "
        + json.dumps(SEATALK_APP_ID)
        + ";\nwindow.PNAR_SEATALK_REDIRECT_URI = "
        + json.dumps(SEATALK_REDIRECT_URI)
        + ";\n"
    )


def seatalk_app_access_token():
    global _seatalk_app_access_token, _seatalk_token_expires_at

    if not SEATALK_APP_SECRET:
        raise ValueError("SeaTalk Login chưa được cấu hình trên server.")

    with _seatalk_token_lock:
        if _seatalk_app_access_token and time.time() < _seatalk_token_expires_at:
            return _seatalk_app_access_token

        try:
            response = requests.post(
                SEATALK_TOKEN_URL,
                json={"app_id": SEATALK_APP_ID, "app_secret": SEATALK_APP_SECRET},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            app.logger.error("SeaTalk access-token request failed (%s).", type(exc).__name__)
            raise RuntimeError("Không thể kết nối dịch vụ đăng nhập SeaTalk.") from exc

        token = payload.get("app_access_token") if isinstance(payload, dict) else None
        expires_at = payload.get("expire") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("code") != 0
            or not isinstance(token, str)
            or not token
            or not isinstance(expires_at, (int, float))
        ):
            app.logger.error("SeaTalk access-token response was invalid.")
            raise RuntimeError("SeaTalk không cấp được phiên đăng nhập.")

        _seatalk_app_access_token = token
        _seatalk_token_expires_at = max(time.time(), expires_at - 60)
        return _seatalk_app_access_token


def get_employee_code_with_email(email):
    token = seatalk_app_access_token()
    try:
        response = requests.post(
            SEATALK_EMPLOYEE_LOOKUP_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"emails": [email]},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("SeaTalk employee lookup failed (%s).", type(exc).__name__)
        raise RuntimeError("Không thể tra cứu tài khoản SeaTalk của bạn.") from exc

    employees = payload.get("employees") if isinstance(payload, dict) else None
    employee_code = (
        employees[0].get("employee_code")
        if isinstance(employees, list) and employees and isinstance(employees[0], dict)
        else None
    )
    if (
        not isinstance(payload, dict)
        or payload.get("code") != 0
        or not isinstance(employee_code, str)
        or not employee_code
    ):
        app.logger.error("SeaTalk employee lookup returned no employee code.")
        raise RuntimeError("Không tìm thấy mã nhân viên SeaTalk cho email đăng nhập.")
    return employee_code


def seatalk_employee_code(user):
    employee_code = user.get("employee_code")
    if isinstance(employee_code, str) and employee_code:
        return employee_code
    email = user.get("email")
    if not isinstance(email, str) or "@" not in email:
        raise RuntimeError("Không xác định được email tài khoản để gửi SeaTalk.")
    return get_employee_code_with_email(email)


def seatalk_api_post(url, payload):
    token = seatalk_app_access_token()
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("SeaTalk message request failed (%s).", type(exc).__name__)
        raise RuntimeError("Không thể gửi tin nhắn qua SeaTalk.") from exc

    if not isinstance(result, dict) or result.get("code") != 0:
        app.logger.error("SeaTalk rejected a message request.")
        raise RuntimeError("SeaTalk không chấp nhận tin nhắn.")
    return result


def extract_google_doc_text(value):
    if isinstance(value, dict):
        text_run = value.get("textRun")
        if isinstance(text_run, dict):
            content = text_run.get("content")
            return content if isinstance(content, str) else ""
        return "".join(extract_google_doc_text(child) for child in value.values())
    if isinstance(value, list):
        return "".join(extract_google_doc_text(child) for child in value)
    return ""


def download_ai_html_output():
    try:
        token = get_access_token(GOOGLE_DOCS_READ_SCOPES)
        with requests.get(
            GOOGLE_DOCS_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            stream=True,
            timeout=(10, 60),
        ) as response:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_SEATALK_HTML_SIZE:
                raise RuntimeError("File HTML vượt quá giới hạn 20 MB gửi qua SeaTalk.")

            content = bytearray()
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                content.extend(chunk)
                if len(content) > MAX_SEATALK_HTML_SIZE:
                    raise RuntimeError("Tài liệu HTML vượt quá giới hạn 20 MB.")
            document = json.loads(bytes(content))
            if not isinstance(document, dict):
                raise RuntimeError("Google Docs trả về dữ liệu tài liệu không hợp lệ.")
            body = document.get("body")
            body_content = body.get("content") if isinstance(body, dict) else None
            if not isinstance(body_content, list):
                raise RuntimeError("Google Docs không trả về nội dung tài liệu.")
            html_content = extract_google_doc_text(body_content)
            html_content = html_content.encode("utf-8")
            if not re.search(rb"<[a-zA-Z][^>]*>", html_content[:4096]):
                raise RuntimeError("Google Docs không trả về nội dung HTML hợp lệ.")
    except SheetDataError as exc:
        raise RuntimeError(str(exc)) from exc
    except requests.HTTPError as exc:
        response = exc.response
        status = response.status_code if response is not None else "không rõ"
        try:
            error = response.json().get("error", {}) if response is not None else {}
        except ValueError:
            error = {}
        message = error.get("message", "") if isinstance(error, dict) else ""
        app.logger.error(
            "Google Docs API returned HTTP %s: %s",
            status,
            message[:500] if isinstance(message, str) else "unknown error",
        )
        if status == 403 and isinstance(message, str):
            lowered_message = message.lower()
            if "has not been used" in lowered_message or "disabled" in lowered_message:
                raise RuntimeError(
                    "Google Docs API chưa được bật trong Google Cloud project "
                    "của ứng dụng. Hãy bật Google Docs API rồi thử lại."
                ) from exc
            raise RuntimeError(
                "Google Docs API từ chối quyền truy cập. Hãy kiểm tra tài khoản "
                "OAuth có quyền xem tài liệu."
            ) from exc
        raise RuntimeError(f"Google Docs API trả lỗi HTTP {status}.") from exc
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("Could not retrieve Google Docs content (%s).", type(exc).__name__)
        raise RuntimeError("Không thể tải file HTML từ Google Docs.") from exc

    return "analysis_output_html.html", html_content


def send_seatalk_ai_reply(user, reply):
    employee_code = seatalk_employee_code(user)
    html_file = (
        download_ai_html_output()
        if "analysis_output_html.html" in reply
        else None
    )
    text_content = re.sub(
        r"\[([^\]]+)\]\(https://docs\.google\.com/document/d/"
        r"1Sn5pBv10g-xVSIaWpQzNZ6uyUfTxRhQhMUh2mqeIevM/[^)\s]*\)",
        r"\1",
        reply,
    )

    seatalk_api_post(
        SEATALK_INIT_STREAM_URL,
        {
            "employee_code": employee_code,
            "message": {
                "tag": "text",
                "text": {"format": 1, "content": text_content},
            },
        },
    )
    if html_file:
        filename, file_bytes = html_file
        try:
            seatalk_api_post(
                SEATALK_SINGLE_CHAT_URL,
                {
                    "employee_code": employee_code,
                    "message": {
                        "tag": "file",
                        "file": {
                            "content": base64.b64encode(file_bytes).decode("ascii"),
                            "filename": filename,
                        },
                    },
                },
            )
        except RuntimeError as exc:
            app.logger.error("SeaTalk text was sent, but HTML delivery failed: %s", exc)
            return {
                "sent": True,
                "file_sent": False,
                "warning": "Câu trả lời đã được gửi nhưng file HTML chưa gửi được.",
            }
    return {"sent": True, "file_sent": html_file is not None}


def exchange_seatalk_code(code):
    token = seatalk_app_access_token()
    try:
        response = requests.get(
            SEATALK_CODE_EXCHANGE_URL,
            params={"code": code},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("SeaTalk identity request failed (%s).", type(exc).__name__)
        raise RuntimeError("Không thể xác minh tài khoản SeaTalk.") from exc

    employee = payload.get("employee") if isinstance(payload, dict) else None
    email = employee.get("email") if isinstance(employee, dict) else None
    name = employee.get("name") if isinstance(employee, dict) else None
    employee_code = employee.get("employee_code") if isinstance(employee, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("code") != 0
        or not isinstance(email, str)
        or "@" not in email
    ):
        app.logger.error("SeaTalk identity response was invalid.")
        raise RuntimeError("SeaTalk không trả về email hợp lệ cho tài khoản.")

    user = {
        "email": email,
        "name": name.strip() if isinstance(name, str) and name.strip() else email.split("@", 1)[0],
        "provider": "seatalk",
    }
    if isinstance(employee_code, str) and employee_code:
        user["employee_code"] = employee_code
    return user


def handle_seatalk_callback():
    returned_state = request.args.get("state", "")
    pending_login = session.get("seatalk_oauth")
    expected_state = pending_login.get("state") if isinstance(pending_login, dict) else None
    expires_at = pending_login.get("expires_at") if isinstance(pending_login, dict) else None

    if (
        not isinstance(expected_state, str)
        or not isinstance(expires_at, (int, float))
        or time.time() >= expires_at
        or not isinstance(returned_state, str)
        or not hmac.compare_digest(returned_state, expected_state)
    ):
        return redirect("/?seatalk_error=state")

    session.pop("seatalk_oauth", None)
    if request.args.get("error"):
        return redirect("/?seatalk_error=denied")

    code = request.args.get("code", "")
    if not code or len(code) > 2048:
        return redirect("/?seatalk_error=code")

    try:
        user = exchange_seatalk_code(code)
    except (RuntimeError, ValueError):
        return redirect("/?seatalk_error=exchange")

    session.clear()
    session.permanent = True
    session["user"] = user
    app.logger.info("SeaTalk sign-in succeeded for %s", user["email"])
    return redirect("/")


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


def get_access_token(required_scopes=None):
    global _oauth_credentials

    required_scopes = required_scopes or SHEETS_READ_SCOPES
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
            if not granted_scopes.intersection(required_scopes):
                if required_scopes == GOOGLE_DOCS_READ_SCOPES:
                    raise SheetDataError(
                        "token.json chưa được cấp quyền đọc Google Docs. "
                        "Hãy xóa token.json cũ rồi khởi động lại app để đăng nhập "
                        "OAuth và cấp quyền Google Docs."
                    )
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


def invoke_ai_agent(message, thread_id, user_id, files=None):
    if not AI_AGENT_ENDPOINT_KEY:
        raise ValueError(
            "AI chưa được cấu hình. Hãy đặt AI_AGENT_ENDPOINT_KEY "
            "rồi khởi động lại server."
        )

    if files:
        form_data = {
            "endpoint_deployment_hash_id": AI_AGENT_DEPLOYMENT_HASH_ID,
            "endpoint_deployment_key": AI_AGENT_ENDPOINT_KEY,
            "user_id": user_id,
            "input_str": message,
            "thread_id": thread_id,
            "mode": "sync",
            "streaming": "false",
            "auto_clean_up_session_files": "false",
            "tool_variables": "{}",
            "override_tool_variables": "{}",
        }
        uploaded_files = []
        for uploaded_file in files:
            filename = uploaded_file.filename
            content_type = (
                "text/csv"
                if Path(filename).suffix.lower() == ".csv"
                else mimetypes.guess_type(filename)[0]
                or uploaded_file.mimetype
                or "application/octet-stream"
            )
            uploaded_files.append(
                ("files", (filename, uploaded_file.stream, content_type))
            )
        response = None
        try:
            response = requests.post(
                AI_AGENT_FILES_URL,
                data=form_data,
                files=uploaded_files,
                timeout=600,
            )
            response.raise_for_status()
            result = response.json()
            app.logger.info(
                "AI agent file response envelope: %s",
                json.dumps(result, ensure_ascii=False),
            )
            response.close()
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "không rõ"
            details = exc.response.text[:500] if exc.response is not None else str(exc)
            if exc.response is not None:
                exc.response.close()
            app.logger.error("AI agent returned HTTP %s: %s", status, details)
            raise RuntimeError(f"Agent AI trả lỗi HTTP {status}.") from exc
        except (requests.RequestException, ValueError) as exc:
            app.logger.error("AI agent request failed: %s", exc)
            if response is not None:
                response.close()
            raise RuntimeError(
                "Không thể kết nối hoặc đọc phản hồi từ Agent AI."
            ) from exc

        if not isinstance(result, dict):
            raise RuntimeError("Agent trả về envelope không hợp lệ.")
        if result.get("status") != "success" or result.get("code") != 0:
            raise RuntimeError(
                result.get("error_message")
                or result.get("error")
                or "Agent trả về lỗi."
            )

        data = result.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError("Agent trả về dữ liệu không hợp lệ.")
        if data.get("is_invoke_error"):
            raise RuntimeError("Agent không thể xử lý yêu cầu này.")

        agent_response = data.get("response") or {}
        if not isinstance(agent_response, dict):
            raise RuntimeError("Agent trả về nội dung phản hồi không hợp lệ.")
        if data.get("is_interrupted"):
            return {
                "reply": agent_response.get("interrupt_message", ""),
                "thread_id": agent_response.get("thread_id") or thread_id,
                "session_id": agent_response.get("session_id")
                or agent_response.get("thread_id")
                or thread_id,
                "interrupted": True,
                "interrupts": agent_response.get("interrupts", []),
            }

        reply = agent_response.get("response_str")
        if not reply:
            raise RuntimeError("Agent không trả về nội dung phản hồi.")
        return {
            "reply": reply,
            "thread_id": agent_response.get("thread_id") or thread_id,
        }

    payload = {
        "endpoint_deployment_hash_id": AI_AGENT_DEPLOYMENT_HASH_ID,
        "endpoint_deployment_key": AI_AGENT_ENDPOINT_KEY,
        "user_id": user_id,
        "message": {"input_str": message, "thread_id": thread_id},
        "mode": "sync",
        "streaming": True,
        "user_credentials": {"token_inputs": []},
        "tool_variables": {},
        "override_tool_variables": {},
        "auto_clean_up_session_files": False,
    }

    response = None
    try:
        response = requests.post(
            AI_AGENT_URL,
            json=payload,
            timeout=600,
            stream=True,
            headers={"Accept": "text/event-stream"},
        )
        response.raise_for_status()
        if "application/json" in response.headers.get("Content-Type", "").lower():
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(
                    "Agent AI trả về JSON không hợp lệ."
                ) from exc
            finally:
                response.close()
            if not isinstance(result, dict):
                raise RuntimeError("Agent trả về envelope không hợp lệ.")
            return result
        return response
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "không rõ"
        details = exc.response.text[:500] if exc.response is not None else str(exc)
        if exc.response is not None:
            exc.response.close()
        app.logger.error("AI agent returned HTTP %s: %s", status, details)
        raise RuntimeError(f"Agent AI trả lỗi HTTP {status}.") from exc
    except requests.RequestException as exc:
        error_response = getattr(exc, "response", None) or response
        status = error_response.status_code if error_response is not None else None
        response_body = error_response.text if error_response is not None else None
        body = response_body[:1000] if response_body is not None else None
        if response_body is not None and len(response_body) > 1000:
            body += "… (đã cắt bớt)"
        app.logger.error(
            "AI agent request failed (status_code=%s, response_body=%r): %s",
            status if status is not None else "không có phản hồi HTTP",
            body if body is not None else "không có response body",
            exc,
        )
        if error_response is not None:
            error_response.close()
        raise RuntimeError("Không thể kết nối hoặc đọc phản hồi từ Agent AI.") from exc


def relay_ai_agent_stream(upstream_response):
    try:
        upstream_response.encoding = upstream_response.encoding or "utf-8"
        for line in upstream_response.iter_lines(decode_unicode=True):
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            yield f"{line}\n"
    except requests.RequestException as exc:
        app.logger.error("AI agent stream interrupted: %s", exc)
        error_event = json.dumps(
            {"error": "Kết nối đến Agent AI bị gián đoạn."},
            ensure_ascii=False,
        )
        yield f"event: response.error\ndata: {error_event}\n\n"
    finally:
        upstream_response.close()


def continue_ai_agent_interrupt(session_id, interrupt_id, status, answers, user_id):
    if not AI_AGENT_ENDPOINT_KEY:
        raise ValueError(
            "AI chưa được cấu hình. Hãy đặt AI_AGENT_ENDPOINT_KEY "
            "rồi khởi động lại server."
        )

    payload = {
        "endpoint_deployment_hash_id": AI_AGENT_DEPLOYMENT_HASH_ID,
        "endpoint_deployment_key": AI_AGENT_ENDPOINT_KEY,
        "user_id": user_id,
        "session_id": session_id,
        "ask_user_response": {
            "interrupt_id": interrupt_id,
            "status": status,
            "answers": answers,
        },
    }

    response = None
    try:
        response = requests.post(
            AI_AGENT_CONTINUE_INTERRUPT_URL,
            json=payload,
            timeout=600,
        )
        response.raise_for_status()
        result = response.json()
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else "không rõ"
        details = exc.response.text[:500] if exc.response is not None else str(exc)
        app.logger.error(
            "AI agent interrupt continuation returned HTTP %s: %s",
            status_code,
            details,
        )
        raise RuntimeError(
            f"Không thể tiếp tục Agent AI (HTTP {status_code})."
        ) from exc
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("AI agent interrupt continuation failed: %s", exc)
        raise RuntimeError("Không thể tiếp tục cuộc hội thoại với Agent AI.") from exc
    finally:
        if response is not None:
            response.close()

    if not isinstance(result, dict):
        raise RuntimeError("Agent trả về envelope không hợp lệ khi tiếp tục.")
    if result.get("status") != "success" or result.get("code") != 0:
        raise RuntimeError(
            result.get("error_message")
            or result.get("error")
            or "Agent không thể tiếp tục yêu cầu."
        )
    return result


@app.get("/")
def home():
    if any(key in request.args for key in ("code", "state", "error")):
        return handle_seatalk_callback()
    if not isinstance(session.get("user"), dict):
        response = send_file(ROOT_DIR / "login-preview.html")
        response.headers["Cache-Control"] = "no-store"
        return response
    response = Response(render_index(), mimetype="text/html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/login-preview")
def login_preview():
    return home()


@app.get("/config.js")
def frontend_config_script():
    return Response(frontend_config(), mimetype="application/javascript")


@app.get("/login_background.png")
def login_background():
    return send_file(ROOT_DIR / "login_background.png", mimetype="image/png")


@app.get("/dior.png")
def dior_avatar():
    return send_file(ROOT_DIR / "dior.png", mimetype="image/png")


@app.get("/header_cover.png")
def header_cover():
    return send_file(ROOT_DIR / "header_cover.png", mimetype="image/png")


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/auth/google")
def google_login():
    if not GOOGLE_OAUTH_WEB_CLIENT_ID:
        return jsonify({"error": "Google Login chưa được cấu hình trên server."}), 503

    body = request.get_json(silent=True)
    credential = body.get("credential") if isinstance(body, dict) else None
    if not isinstance(credential, str) or not credential:
        return jsonify({"error": "Google credential không hợp lệ."}), 400

    try:
        claims = id_token.verify_oauth2_token(
            credential,
            Request(),
            GOOGLE_OAUTH_WEB_CLIENT_ID,
        )
    except (GoogleAuthError, requests.RequestException) as exc:
        app.logger.error("Could not verify Google sign-in token: %s", exc)
        return jsonify({"error": "Không thể kết nối dịch vụ xác minh Google."}), 502
    except ValueError:
        app.logger.info("Rejected invalid Google sign-in credential.")
        return jsonify({"error": "Không thể xác minh tài khoản Google."}), 401

    email = claims.get("email")
    if (
        not isinstance(email, str)
        or not email
        or claims.get("email_verified") is not True
        or not isinstance(claims.get("sub"), str)
    ):
        return jsonify({"error": "Tài khoản Google chưa xác minh email."}), 401

    session.clear()
    session.permanent = True
    name = claims.get("name")
    session["user"] = {
        "email": email,
        "name": name.strip() if isinstance(name, str) and name.strip() else email.split("@", 1)[0],
    }
    app.logger.info("Google sign-in succeeded for %s", email)
    return jsonify({"email": email})


@app.post("/api/auth/seatalk/start")
def seatalk_login_start():
    if not SEATALK_APP_ID or not SEATALK_APP_SECRET:
        return jsonify({"error": "SeaTalk Login chưa được cấu hình trên server."}), 503

    state = secrets.token_urlsafe(32)
    session.permanent = True
    session["seatalk_oauth"] = {
        "state": state,
        "expires_at": time.time() + 600,
    }
    return jsonify({"state": state})


@app.get("/api/auth/me")
def current_user():
    user = session.get("user")
    if not isinstance(user, dict) or not isinstance(user.get("email"), str):
        return jsonify({"error": "Vui lòng đăng nhập bằng Google hoặc SeaTalk."}), 401
    return jsonify({
        "email": user["email"],
        "name": user.get("name") or user["email"].split("@", 1)[0],
    })


@app.get("/api/dashboard-data")
@login_required
def dashboard_data():
    try:
        return jsonify(fetch_dashboard_data())
    except SheetDataError as exc:
        app.logger.error("Google Sheet data request failed: %s", exc)
        return jsonify({"error": str(exc)}), 502


@app.post("/api/ai-chat")
@login_required
def ai_chat():
    files = []
    if request.mimetype == "multipart/form-data":
        message = request.form.get("message")
        thread_id = request.form.get("thread_id", "")
        files = [
            uploaded_file
            for uploaded_file in request.files.getlist("files")
            if uploaded_file.filename
        ]
    else:
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
        result = invoke_ai_agent(
            message.strip(),
            thread_id,
            session["user"]["email"],
            files=files,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 503
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    if isinstance(result, dict):
        return jsonify(result)

    return Response(
        stream_with_context(relay_ai_agent_stream(result)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/ai-chat/continue-interrupt")
@login_required
def continue_ai_chat_interrupt():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body phải là JSON object."}), 400

    session_id = body.get("session_id")
    interrupt_id = body.get("interrupt_id")
    status = body.get("status")
    answers = body.get("answers", [])
    if not isinstance(session_id, str) or not session_id.strip():
        return jsonify({"error": "session_id không hợp lệ."}), 400
    if not isinstance(interrupt_id, str) or not interrupt_id.strip():
        return jsonify({"error": "interrupt_id không hợp lệ."}), 400
    if not isinstance(status, str) or status not in {"answered", "cancelled"}:
        return jsonify({"error": "status phải là answered hoặc cancelled."}), 400
    if not isinstance(answers, list) or any(not isinstance(answer, str) for answer in answers):
        return jsonify({"error": "answers phải là mảng chuỗi."}), 400
    if status == "answered" and (
        not answers or any(not answer.strip() for answer in answers)
    ):
        return jsonify({"error": "Hãy trả lời đầy đủ các câu hỏi của Agent AI."}), 400
    if status == "cancelled":
        answers = []

    try:
        result = continue_ai_agent_interrupt(
            session_id.strip(),
            interrupt_id.strip(),
            status,
            answers,
            session["user"]["email"],
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 503
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502

    return jsonify(result)


@app.post("/api/seatalk/send-ai-reply")
@login_required
def send_ai_reply_to_seatalk():
    body = request.get_json(silent=True)
    reply = body.get("reply") if isinstance(body, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        return jsonify({"error": "Không có câu trả lời AI để gửi."}), 400
    if len(reply) > MAX_SEATALK_REPLY_LENGTH:
        return jsonify({"error": "Câu trả lời AI quá dài để gửi qua SeaTalk."}), 400

    try:
        result = send_seatalk_ai_reply(session["user"], reply.strip())
    except (RuntimeError, ValueError) as exc:
        app.logger.error("Could not send completed AI reply to SeaTalk: %s", exc)
        return jsonify({"error": str(exc)}), 502
    return jsonify(result)


@app.get("/api/ai-chat/html-output")
@login_required
def ai_chat_html_output():
    try:
        filename, html_content = download_ai_html_output()
    except RuntimeError as exc:
        app.logger.error("Could not load generated HTML output: %s", exc)
        return jsonify({"error": str(exc)}), 502

    response = Response(html_content, content_type="text/html; charset=utf-8")
    response.headers["Content-Disposition"] = f'inline; filename="{filename}"'
    response.headers["Content-Security-Policy"] = (
        "sandbox allow-scripts; default-src 'none'; "
        "script-src 'unsafe-inline' https:; style-src 'unsafe-inline' https:; "
        "img-src data: https:; font-src data: https:; connect-src 'none'; "
        "form-action 'none'; base-uri 'none'; object-src 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


if __name__ == "__main__":
    app.logger.info("Starting dashboard on %s:%s", HOST, PORT)
    if serve is not None:
        serve(app, host=HOST, port=PORT)
    else:
        app.run(host=HOST, port=PORT, debug=False)
