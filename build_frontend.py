import argparse
import json
import os
import shutil
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
PARTIALS = {
    "<!-- include:Styles -->": "Styles.html",
    "<!-- include:Script_Filters -->": "Script_Filters.html",
    "<!-- include:Script_Charts -->": "Script_Charts.html",
    "<!-- include:Script_Table -->": "Script_Table.html",
    "<!-- include:Script_Main -->": "Script_Main.html",
}


def build(
    output_dir: Path,
    backend_url: str,
    google_client_id: str,
    seatalk_app_id: str = "",
    seatalk_redirect_uri: str = "",
) -> None:
    seatalk_app_id = seatalk_app_id or os.environ.get(
        "SEATALK_APP_ID", "Nzk2MjY4MzcwMDIz"
    )
    seatalk_redirect_uri = seatalk_redirect_uri or os.environ.get(
        "SEATALK_REDIRECT_URI",
        f"http://127.0.0.1:{os.environ.get('LOCAL_AI_PORT', '8765')}/",
    )

    html = (ROOT_DIR / "index.html").read_text(encoding="utf-8")
    if '<script src="config.js"></script>' not in html:
        html = html.replace(
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>',
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>\n'
            '<script src="config.js"></script>',
        )

    for marker, filename in PARTIALS.items():
        html = html.replace(marker, (ROOT_DIR / filename).read_text(encoding="utf-8"))

    if "<!-- include:" in html:
        raise RuntimeError("index.html contains an unhandled partial include.")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html, encoding="utf-8")

    (output_dir / "config.js").write_text(
        f"window.PNAR_API_BASE_URL = {json.dumps(backend_url.rstrip('/'))};\n"
        f"window.PNAR_GOOGLE_CLIENT_ID = {json.dumps(google_client_id)};\n"
        f"window.PNAR_SEATALK_APP_ID = {json.dumps(seatalk_app_id)};\n"
        f"window.PNAR_SEATALK_REDIRECT_URI = {json.dumps(seatalk_redirect_uri)};\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT_DIR / "login-preview.html", output_dir / "login-preview.html")
    shutil.copy2(ROOT_DIR / "login_background.png", output_dir / "login_background.png")
    shutil.copy2(ROOT_DIR / "dior.png", output_dir / "dior.png")
    shutil.copy2(ROOT_DIR / "header_cover.png", output_dir / "header_cover.png")
    shutil.copy2(ROOT_DIR / "_headers", output_dir / "_headers")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist")
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("FRONTEND_BACKEND_URL", ""),
        help="Public HTTPS URL of the dashboard backend.",
    )
    parser.add_argument(
        "--google-client-id",
        default=os.environ.get("GOOGLE_OAUTH_WEB_CLIENT_ID", ""),
        help="Google OAuth Web client ID.",
    )
    parser.add_argument(
        "--seatalk-app-id",
        default=os.environ.get("SEATALK_APP_ID", "Nzk2MjY4MzcwMDIz"),
        help="SeaTalk Open Platform App ID.",
    )
    parser.add_argument(
        "--seatalk-redirect-uri",
        default=os.environ.get(
            "SEATALK_REDIRECT_URI",
            f"http://127.0.0.1:{os.environ.get('LOCAL_AI_PORT', '8765')}/",
        ),
        help="Exact SeaTalk callback URI registered for the app.",
    )
    args = parser.parse_args()
    output_dir = ROOT_DIR / args.output
    build(
        output_dir,
        args.backend_url,
        args.google_client_id,
        args.seatalk_app_id,
        args.seatalk_redirect_uri,
    )
    print(f"Static frontend built at {output_dir}")


if __name__ == "__main__":
    main()
