# PN/AR Dashboard - chạy local

Ứng dụng chạy bằng Flask trên máy nội bộ, lấy dữ liệu chỉ đọc từ Google Sheet
`raw_data!A:M` qua Google Sheets API. Không cần triển khai bằng Google Apps Script.

## Cài đặt lần đầu

1. Bật **Google Sheets API** trong Google Cloud project của OAuth client.
2. Dùng OAuth client loại **Desktop app**. Đặt file client JSON tên
   `credentials.json` ở thư mục này.
3. Tài khoản Google dùng để OAuth phải có quyền xem spreadsheet.
4. Nếu chưa có virtual environment, tạo trước:

   ```powershell
   py -m venv .venv
   ```

5. Cài dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

## Khởi động và truy cập từ LAN

Chạy `run_local.bat`, hoặc:

```powershell
$env:LOCAL_AI_HOST = "0.0.0.0"
$env:LOCAL_AI_PORT = "8765"
.\.venv\Scripts\python.exe app.py
```

Lần đầu, OAuth mở trình duyệt trên chính máy chạy server để đăng nhập và cấp
quyền đọc Sheet. Token được lưu vào `token.json` và tự làm mới khi hết hạn.
Không chia sẻ hoặc commit `credentials.json` hay `token.json`.

Nếu API báo `token.json` thiếu quyền đọc Google Sheets, token hiện tại đã được
cấp cho scope khác. Xóa riêng file `token.json`, khởi động lại app rồi đăng nhập
và chấp thuận quyền Google Sheets khi trình duyệt OAuth mở ra.

- Trên máy server: mở `http://127.0.0.1:8765`.
- Trên máy khác cùng mạng: mở `http://<IPv4-của-máy-server>:8765`.
- Có thể kiểm tra server tại `http://<IPv4-của-máy-server>:8765/health`.

Nếu máy khách không kết nối được, cho phép Python hoặc cổng TCP 8765 qua
Windows Firewall trên **Private network**. Không chuyển tiếp cổng này ra Internet.

## Cấu hình AI Assistant

Đặt endpoint key và user ID được cấp cho Agent trước khi khởi động server.
Giữ endpoint key ở biến môi trường phía server; không đưa key vào HTML/JavaScript
hoặc chia sẻ công khai.

```powershell
$env:AI_AGENT_ENDPOINT_KEY = "<ENDPOINT_KEY>"
$env:AI_AGENT_USER_ID = "<USER_ID>"
.\run_local.bat
```

Backend gửi yêu cầu streaming đến deployment đã cấu hình và relay SSE để câu trả
lời hiển thị dần trong bảng chat. Frontend lưu `session_id` từ event
`response.start` để tiếp tục hội thoại hiện tại. Dùng **Chat mới** để bắt đầu
một thread khác. Có thể thay deployment bằng `AI_AGENT_DEPLOYMENT_HASH_ID`.

## Cấu hình tùy chọn

Mặc định server lắng nghe `0.0.0.0:8765`. Có thể đặt:

- `LOCAL_AI_HOST`, `LOCAL_AI_PORT`: địa chỉ và cổng server.
- `AI_AGENT_ENDPOINT_KEY`, `AI_AGENT_USER_ID`: thông tin cấu hình Agent AI.
- `AI_AGENT_DEPLOYMENT_HASH_ID`: deployment hash của Agent (mặc định theo cấu hình project).
- `GOOGLE_OAUTH_CREDENTIALS`, `GOOGLE_OAUTH_TOKEN`: đường dẫn hai file OAuth.
- `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_RANGE`: spreadsheet và vùng dữ liệu.

Ví dụ, đặt các đường dẫn OAuth không nằm trong thư mục project:

```powershell
$env:GOOGLE_OAUTH_CREDENTIALS = "C:\secure\credentials.json"
$env:GOOGLE_OAUTH_TOKEN = "C:\secure\token.json"
```

## Triển khai frontend lên Cloudflare Pages

Frontend được build thành HTML/CSS/JS static trong thư mục `dist`.
Backend Flask chạy riêng trên VPS hoặc máy vật lý có IP cố định.

1. Cho backend phép gọi origin Cloudflare bằng biến môi trường:

   ```powershell
   $env:FRONTEND_ORIGINS = "https://pn-ar-dashboard.pages.dev,https://dashboard.example.com"
   ```

2. Build static frontend, trỏ đến backend HTTPS công khai:

   ```powershell
   $env:FRONTEND_BACKEND_URL = "https://api.example.com"
   .\.venv\Scripts\python.exe build_frontend.py
   ```

3. Trên Cloudflare Pages:

   - Framework preset: `None`
   - Build command: `python build_frontend.py`
   - Build output directory: `dist`
   - Environment variable: `FRONTEND_BACKEND_URL=https://api.example.com`

Không đặt API key Monee, Google OAuth token, Agent endpoint key hoặc secret
khác trong frontend hoặc trong repository static.
