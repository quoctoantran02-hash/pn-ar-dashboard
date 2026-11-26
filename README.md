# PN/AR Dashboard - chạy local

Ứng dụng chạy bằng Flask, đăng nhập người dùng bằng Google hoặc SeaTalk và lấy
dữ liệu chỉ đọc từ Google Sheet `raw_data!A:M` qua OAuth Desktop riêng của ứng
dụng. Không cần triển khai bằng Google Apps Script.

## Cài đặt lần đầu

1. Tạo OAuth client loại **Web application** trong Google Cloud Console. Thêm
   Authorized JavaScript origins:

   - `http://localhost:8765`
   - `http://127.0.0.1:8765`
   - `https://pn-ar-dashboard.quoctoan-tran02.workers.dev`

   Cấu hình OAuth consent screen và thêm user thử nghiệm nếu ứng dụng đang ở
   trạng thái Testing. Luồng Google Identity Services này không cần Authorized
   redirect URI.
   Đặt Client ID vào biến môi trường `GOOGLE_OAUTH_WEB_CLIENT_ID`. Client ID
   này dùng cho đăng nhập website, không thay thế `credentials.json`.
2. Bật **Google Sheets API** trong Google Cloud project của OAuth client đọc Sheet.
3. Dùng OAuth client loại **Desktop app** cho Google Sheets. Đặt file client JSON tên
   `credentials.json` ở thư mục này.
4. Tài khoản Google dùng cho OAuth Desktop phải có quyền xem spreadsheet.
5. Nếu chưa có virtual environment, tạo trước:

   ```powershell
   py -m venv .venv
   ```

6. Cài dependencies:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

## Khởi động và đăng nhập local

Thiết lập Client ID web và key Agent trước khi khởi chạy:

```powershell
$env:GOOGLE_OAUTH_WEB_CLIENT_ID = "<WEB_CLIENT_ID>.apps.googleusercontent.com"
$env:SEATALK_APP_SECRET = "<SEATALK_APP_SECRET>"
$env:AI_AGENT_ENDPOINT_KEY = "<ENDPOINT_KEY>"
$env:LOCAL_AI_HOST = "0.0.0.0"
$env:LOCAL_AI_PORT = "8765"
$env:SEATALK_REDIRECT_URI = "http://127.0.0.1:8765/"
.\.venv\Scripts\python.exe app.py
```

Mở `http://127.0.0.1:8765` để đăng nhập. Dùng đúng host `127.0.0.1` vì đây là
host trong SeaTalk Redirect URI; `localhost` và `127.0.0.1` không dùng chung
cookie phiên. Khi chạy local, đặt `SEATALK_REDIRECT_URI` như trên để ghi đè
Worker URL mặc định. SeaTalk `state` được tạo và kiểm tra ở backend. Sau khi xác minh
state, backend đổi authorization code lấy email hồ sơ SeaTalk rồi tạo phiên
đăng nhập 24 giờ. Phiên được ký bằng key cục bộ trong `.flask_session_secret`.
Không xóa key này khi server đang có phiên hoạt động; không chia sẻ hoặc commit
file này.

Trong SeaTalk Open Platform, cấu hình Redirect URI chính xác là
`http://127.0.0.1:8765/`. `SEATALK_APP_SECRET` chỉ được đặt ở backend; không
đưa secret vào frontend hoặc repository.

OAuth Desktop đọc Google Sheet vẫn chạy riêng: lần đầu, trình duyệt trên máy
server sẽ mở để cấp quyền, token được lưu vào `token.json` và tự làm mới khi
hết hạn. Không chia sẻ hoặc commit `credentials.json` hay `token.json`.

Nếu API báo `token.json` thiếu quyền đọc Google Sheets, token hiện tại đã được
cấp cho scope khác. Xóa riêng file `token.json`, khởi động lại app rồi đăng nhập
và chấp thuận quyền Google Sheets khi trình duyệt OAuth mở ra.

- Trên máy server: mở `http://127.0.0.1:8765`.
- Máy khác trong LAN cần HTTPS để dùng Google Login; Google không hỗ trợ ổn định
  luồng này trên origin HTTP tùy ý.
- Có thể kiểm tra server tại `http://<IPv4-của-máy-server>:8765/health`.

Nếu máy khách không kết nối được, cho phép Python hoặc cổng TCP 8765 qua
Windows Firewall trên **Private network**. Không chuyển tiếp cổng này ra Internet.

## Cấu hình AI Assistant

Chỉ cần endpoint key phía server. Backend tự dùng email Google đã xác minh của
người đăng nhập làm `user_id` khi gọi Agent; không cần đặt `AI_AGENT_USER_ID`.
Không đưa endpoint key vào HTML/JavaScript hoặc chia sẻ công khai.

```powershell
$env:AI_AGENT_ENDPOINT_KEY = "<ENDPOINT_KEY>"
.\run_local.bat
```

Backend gửi yêu cầu streaming đến deployment đã cấu hình và relay SSE để câu trả
lời hiển thị dần trong bảng chat. Frontend lưu `session_id` từ event
`response.start` để tiếp tục hội thoại hiện tại. Dùng **Chat mới** để bắt đầu
một thread khác. Lịch sử và thread được lưu trong trình duyệt theo email đăng
nhập và hết hạn sau 24 giờ. Có thể thay deployment bằng
`AI_AGENT_DEPLOYMENT_HASH_ID`.

Nếu Agent tạm dừng với interrupt `ask_user`, giao diện hiển thị `interrupt_message`
và từng câu hỏi để người dùng trả lời theo đúng thứ tự. Backend tiếp tục cùng
`session_id` qua `continue_agent_v2_interrupt`; nếu Agent lại hỏi, giao diện dùng
`interrupt_id` mới nhất. Người dùng cũng có thể chọn **Không trả lời** để gửi
`status: cancelled`.

Khi người dùng chọn gửi phản hồi chờ lâu qua SeaTalk, nút **Oke** sẽ gửi câu trả
lời sau khi AI hoàn tất. Backend dùng employee code có trong phiên SeaTalk hoặc
tra cứu theo email đăng nhập; nếu phản hồi có PDF từ kho tài liệu AI, backend
gửi nội dung và file PDF vào cuộc trò chuyện SeaTalk. Tính năng này cần
`SEATALK_APP_SECRET` được cấu hình.

## Cấu hình tùy chọn

Mặc định server lắng nghe `0.0.0.0:8765`. Có thể đặt:

- `LOCAL_AI_HOST`, `LOCAL_AI_PORT`: địa chỉ và cổng server.
- `GOOGLE_OAUTH_WEB_CLIENT_ID`: Client ID đăng nhập website (Web application).
- `SEATALK_APP_ID`: SeaTalk Open Platform App ID (mặc định là App ID của project).
- `SEATALK_APP_SECRET`: SeaTalk App Secret, bắt buộc cho đăng nhập SeaTalk.
- `SEATALK_REDIRECT_URI`: URI callback chính xác đã đăng ký trong SeaTalk; mặc
  định `http://127.0.0.1:8765/`.
- `AI_AGENT_ENDPOINT_KEY`: thông tin endpoint Agent AI.
- `AI_AGENT_DEPLOYMENT_HASH_ID`: deployment hash của Agent (mặc định theo cấu hình project).
- `GOOGLE_OAUTH_CREDENTIALS`, `GOOGLE_OAUTH_TOKEN`: đường dẫn hai file OAuth.
- `GOOGLE_SHEET_ID`, `GOOGLE_SHEET_RANGE`: spreadsheet và vùng dữ liệu.
- `FLASK_SECRET_KEY`: tùy chọn; mặc định app tạo key ký phiên tại
  `.flask_session_secret`.
- `SESSION_COOKIE_SECURE`: bật `1` khi phục vụ qua HTTPS.

Ví dụ, đặt các đường dẫn OAuth không nằm trong thư mục project:

```powershell
$env:GOOGLE_OAUTH_CREDENTIALS = "C:\secure\credentials.json"
$env:GOOGLE_OAUTH_TOKEN = "C:\secure\token.json"
```

## Triển khai frontend lên Cloudflare Worker

Frontend được build thành HTML/CSS/JS static trong thư mục `dist`; Worker trong
`src/index.js` chuyển tiếp `/api/*` và chọn trang đăng nhập/dashboard theo phiên.
Backend Flask chạy riêng trên VPS hoặc máy vật lý có IP cố định.

1. Cấu hình biến môi trường trên backend:

   ```powershell
   $env:GOOGLE_OAUTH_WEB_CLIENT_ID = "<WEB_CLIENT_ID>.apps.googleusercontent.com"
   $env:FLASK_SECRET_KEY = "<LONG_RANDOM_SECRET>"
   $env:FRONTEND_ORIGINS = "https://pn-ar-dashboard.quoctoan-tran02.workers.dev"
   ```

   Giữ `FLASK_SECRET_KEY` ổn định qua các lần khởi động backend để các phiên
   trong 24 giờ không bị mất. SeaTalk Redirect URI mặc định được đặt trong code
   là `https://pn-ar-dashboard.quoctoan-tran02.workers.dev/`, nên cookie session
   tự bật `Secure`. Chỉ đặt `SEATALK_REDIRECT_URI` hoặc
   `SESSION_COOKIE_SECURE` nếu cần ghi đè mặc định.

2. Đặt cùng `GOOGLE_OAUTH_WEB_CLIENT_ID` trong môi trường build frontend rồi build.
   Redirect URI mặc định giống backend; chỉ cần ghi đè `SEATALK_REDIRECT_URI`
   nếu dùng Worker domain khác:

   ```powershell
   $env:FRONTEND_BACKEND_URL = ""
   .\.venv\Scripts\python.exe build_frontend.py
   ```

3. Đặt Redirect URI mặc định trên trong cấu hình SeaTalk Open Platform, rồi
   deploy Worker và thư mục `dist` theo cấu hình `wrangler.toml`. Callback tại
   Worker root được chuyển tiếp tới backend để kiểm tra state và hoàn tất phiên.
   Dùng domain Worker đã khai báo trong Authorized JavaScript origins của Google
   OAuth client.

Không đặt API key Monee, Google OAuth token, Agent endpoint key hoặc secret
khác trong frontend hoặc trong repository static.
