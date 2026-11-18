import base64
import json
import unittest
from unittest.mock import MagicMock, patch

import app as dashboard
import requests


class AIHtmlOutputTests(unittest.TestCase):
    def setUp(self):
        dashboard.app.config["TESTING"] = True
        self.client = dashboard.app.test_client()

    def test_downloads_raw_html_from_google_docs_api(self):
        html_content = "<!doctype html><html><body>report</body></html>"
        document = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": html_content}}]
                        }
                    }
                ]
            }
        }
        document_bytes = json.dumps(document).encode("utf-8")
        response = MagicMock()
        response.headers = {"Content-Length": str(len(document_bytes))}
        response.iter_content.return_value = [document_bytes]
        response.raise_for_status.return_value = None

        with patch.object(dashboard, "get_access_token", return_value="docs-token") as token:
            with patch.object(dashboard.requests, "get") as mock_get:
                mock_get.return_value.__enter__.return_value = response
                filename, content = dashboard.download_ai_html_output()

        self.assertEqual(filename, "analysis_output_html.html")
        self.assertEqual(content, html_content.encode("utf-8"))
        token.assert_called_once_with(dashboard.GOOGLE_DOCS_READ_SCOPES)
        mock_get.assert_called_once_with(
            dashboard.GOOGLE_DOCS_API_URL,
            headers={"Authorization": "Bearer docs-token"},
            stream=True,
            timeout=(10, 60),
        )

    def test_docs_api_disabled_error_is_actionable(self):
        response = MagicMock()
        response.status_code = 403
        response.json.return_value = {
            "error": {
                "message": "Google Docs API has not been used or it is disabled."
            }
        }
        http_error = requests.HTTPError(response=response)

        with patch.object(dashboard, "get_access_token", return_value="docs-token"):
            with patch.object(dashboard.requests, "get", side_effect=http_error):
                with self.assertRaisesRegex(RuntimeError, "Google Docs API chưa được bật"):
                    dashboard.download_ai_html_output()

    @patch.object(dashboard, "seatalk_api_post", return_value={"code": 0})
    @patch.object(
        dashboard,
        "download_ai_html_output",
        return_value=("analysis_output_html.html", b"<html>report</html>"),
    )
    @patch.object(dashboard, "seatalk_employee_code", return_value="employee-1")
    def test_seatalk_attaches_html_only_when_reply_mentions_filename(
        self, _mock_employee_code, mock_download, mock_seatalk_post
    ):
        no_file_result = dashboard.send_seatalk_ai_reply(
            {"email": "person@example.com"},
            "The analysis is complete.",
        )
        self.assertFalse(no_file_result["file_sent"])
        mock_download.assert_not_called()
        mock_seatalk_post.assert_called_once()

        mock_seatalk_post.reset_mock()
        result = dashboard.send_seatalk_ai_reply(
            {"email": "person@example.com"},
            "Created analysis_output_html.html.",
        )

        self.assertTrue(result["file_sent"])
        mock_download.assert_called_once()
        self.assertEqual(mock_seatalk_post.call_count, 2)
        file_payload = mock_seatalk_post.call_args_list[1].args[1]["message"]["file"]
        self.assertEqual(file_payload["filename"], "analysis_output_html.html")
        self.assertEqual(
            base64.b64decode(file_payload["content"]),
            b"<html>report</html>",
        )

    @patch.object(
        dashboard,
        "download_ai_html_output",
        return_value=("analysis_output_html.html", b"<html>report</html>"),
    )
    def test_html_output_route_opens_the_file_inline(self, _mock_download):
        with self.client.session_transaction() as user_session:
            user_session["user"] = {"email": "person@example.com"}

        response = self.client.get("/api/ai-chat/html-output")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith("text/html"))
        self.assertIn("inline", response.headers["Content-Disposition"])
        self.assertIn("sandbox", response.headers["Content-Security-Policy"])
        self.assertEqual(response.data, b"<html>report</html>")


if __name__ == "__main__":
    unittest.main()
