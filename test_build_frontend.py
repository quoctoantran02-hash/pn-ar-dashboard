import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from build_frontend import DEFAULT_SEATALK_REDIRECT_URI, build


class FrontendBuildTests(unittest.TestCase):
    def test_build_uses_default_seatalk_redirect_uri_without_environment_setting(self):
        with tempfile.TemporaryDirectory() as output_dir:
            with patch.dict(os.environ, {"SEATALK_REDIRECT_URI": ""}):
                build(Path(output_dir), "", "")

            config = (Path(output_dir) / "config.js").read_text(encoding="utf-8")

        self.assertIn(
            f"window.PNAR_SEATALK_REDIRECT_URI = \"{DEFAULT_SEATALK_REDIRECT_URI}\";",
            config,
        )


if __name__ == "__main__":
    unittest.main()
