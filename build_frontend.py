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


def build(output_dir: Path, backend_url: str) -> None:
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
        f"window.PNAR_API_BASE_URL = {json.dumps(backend_url.rstrip('/'))};\n",
        encoding="utf-8",
    )
    shutil.copy2(ROOT_DIR / "dior.png", output_dir / "dior.png")
    shutil.copy2(ROOT_DIR / "_headers", output_dir / "_headers")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="dist")
    parser.add_argument(
        "--backend-url",
        default="",
        help="Public HTTPS URL of the dashboard backend.",
    )
    args = parser.parse_args()
    output_dir = ROOT_DIR / args.output
    build(output_dir, args.backend_url)
    print(f"Static frontend built at {output_dir}")


if __name__ == "__main__":
    main()
