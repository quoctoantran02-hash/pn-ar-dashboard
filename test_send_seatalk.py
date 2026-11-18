import os
import requests
import base64

APP_ID = "Nzk2MjY4MzcwMDIz"
APP_SECRET = os.environ.get("SEATALK_APP_SECRET", "")

BASE_URL = "https://openapi.seatalk.io"


def get_app_access_token():
    if not APP_SECRET:
        raise RuntimeError("Set the SEATALK_APP_SECRET environment variable.")

    url = f"{BASE_URL}/auth/app_access_token"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 0:
        raise Exception(f"Get access token failed: {data}")

    return data["app_access_token"]


def get_employee_code_with_email(email):
    url = f"{BASE_URL}/contacts/v2/get_employee_code_with_email"
    headers = {
        "Authorization": f"Bearer {get_app_access_token()}",
        "Content-Type": "application/json"
    }
    payload = {
        "emails": [email]
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != 0:
        raise Exception(f"Get employee code failed: {data}")

    employees = data.get("employees", [])
    if not employees:
        raise Exception("No employee returned for this email")

    # pick the first matched employee_code
    employee_code = employees[0].get("employee_code")
    if not employee_code:
        raise Exception(f"No employee_code found in response: {data}")

    return employee_code


def send_file_to_user(employee_code, file_content, filename):
    """
    file_content: Base64-encoded file content
    filename: e.g. "demo.pdf"
    """
    url = f"{BASE_URL}/messaging/v2/single_chat"
    headers = {
        "Authorization": f"Bearer {get_app_access_token()}",
        "Content-Type": "application/json"
    }

    payload = {
        "employee_code": employee_code,
        "message": {
            "tag": "file",
            "file": {
                "content": file_content,
                "filename": filename
            }
        }
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_streaming_message_to_user(employee_code, content):
    url = f"{BASE_URL}/messaging/v2/single_chat/init_stream"
    headers = {
        "Authorization": f"Bearer {get_app_access_token()}",
        "Content-Type": "application/json"
    }

    payload = {
        "employee_code": employee_code,
        "message": {
            "tag": "text",
            "text": {
                "format": 1,
                "content": content
            }
        }
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    target_email = os.environ.get("SEATALK_TEST_EMAIL", "")
    if not target_email:
        raise RuntimeError("Set the SEATALK_TEST_EMAIL environment variable.")

    # Example: Base64-encoded file content
    # Replace this with your real file content

    filename = "demo.csv"

    employee_code = get_employee_code_with_email(target_email)
    print("employee_code:", employee_code)

    with open("sample_attach_file.csv", "rb") as f:
        file_content = base64.b64encode(f.read()).decode("utf-8")

    result1 = send_streaming_message_to_user(employee_code, content="Hello, this is a streaming message test.")
    result2 = send_file_to_user(employee_code, file_content, filename)