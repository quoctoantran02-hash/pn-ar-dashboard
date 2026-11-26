import time
import unittest
from unittest.mock import Mock, patch

import app as dashboard


class SeaTalkAuthTests(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()
        dashboard.app.config["TESTING"] = True
        dashboard.SEATALK_APP_SECRET = "test-seatalk-secret"
        dashboard._seatalk_app_access_token = ""
        dashboard._seatalk_token_expires_at = 0

    def test_secure_session_cookie_defaults_to_https_callback(self):
        self.assertTrue(
            dashboard.session_cookie_secure_setting(
                None,
                "https://dashboard.example/",
            )
        )
        self.assertFalse(
            dashboard.session_cookie_secure_setting(
                None,
                "http://127.0.0.1:8765/",
            )
        )
        self.assertFalse(
            dashboard.session_cookie_secure_setting(
                "0",
                "https://dashboard.example/",
            )
        )

    def start_login(self):
        response = self.client.post("/api/auth/seatalk/start")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["state"]

    @staticmethod
    def json_response(payload):
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_start_creates_backend_state_and_permanent_session(self):
        state = self.start_login()

        self.assertGreaterEqual(len(state), 40)
        with self.client.session_transaction() as user_session:
            self.assertTrue(user_session.permanent)
            self.assertEqual(user_session["seatalk_oauth"]["state"], state)

    @patch.object(dashboard.requests, "get")
    @patch.object(dashboard.requests, "post")
    def test_callback_exchanges_code_and_creates_session(self, mock_post, mock_get):
        state = self.start_login()
        mock_post.return_value = self.json_response(
            {"code": 0, "app_access_token": "access-token", "expire": time.time() + 3600}
        )
        mock_get.return_value = self.json_response(
            {
                "code": 0,
                "employee": {
                    "email": "person@example.com",
                    "name": "SeaTalk User",
                },
            }
        )

        response = self.client.get(f"/?code=auth-code&state={state}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        mock_post.assert_called_once_with(
            dashboard.SEATALK_TOKEN_URL,
            json={
                "app_id": dashboard.SEATALK_APP_ID,
                "app_secret": "test-seatalk-secret",
            },
            timeout=10,
        )
        mock_get.assert_called_once_with(
            dashboard.SEATALK_CODE_EXCHANGE_URL,
            params={"code": "auth-code"},
            headers={"Authorization": "Bearer access-token"},
            timeout=10,
        )

        user_response = self.client.get("/api/auth/me")
        self.assertEqual(user_response.get_json()["email"], "person@example.com")
        self.assertEqual(user_response.get_json()["name"], "SeaTalk User")
        with self.client.session_transaction() as user_session:
            self.assertTrue(user_session.permanent)
            self.assertEqual(user_session["user"]["provider"], "seatalk")
            self.assertNotIn("seatalk_oauth", user_session)

        replay = self.client.get(f"/?code=auth-code&state={state}")
        self.assertIn("seatalk_error=state", replay.headers["Location"])
        mock_get.assert_called_once()

    @patch.object(dashboard.requests, "get")
    @patch.object(dashboard.requests, "post")
    def test_callback_rejects_mismatched_state_without_exchange(self, mock_post, mock_get):
        self.start_login()

        with self.assertLogs(dashboard.app.logger.name, level="WARNING") as logs:
            response = self.client.get("/?code=auth-code&state=incorrect")

        self.assertIn("seatalk_error=state", response.headers["Location"])
        self.assertIn(
            "SeaTalk callbackError=state reason=state_mismatch",
            logs.output[0],
        )
        mock_post.assert_not_called()
        mock_get.assert_not_called()

    @patch.object(dashboard.requests, "get")
    @patch.object(dashboard.requests, "post")
    def test_callback_rejects_expired_state(self, mock_post, mock_get):
        state = self.start_login()
        with self.client.session_transaction() as user_session:
            pending_login = user_session["seatalk_oauth"]
            pending_login["expires_at"] = time.time() - 1
            user_session["seatalk_oauth"] = pending_login

        response = self.client.get(f"/?code=auth-code&state={state}")

        self.assertIn("seatalk_error=state", response.headers["Location"])
        mock_post.assert_not_called()
        mock_get.assert_not_called()

    @patch.object(dashboard.requests, "get")
    @patch.object(dashboard.requests, "post")
    def test_seatalk_denial_is_state_validated(self, mock_post, mock_get):
        state = self.start_login()

        response = self.client.get(f"/?error=access_denied&state={state}")

        self.assertIn("seatalk_error=denied", response.headers["Location"])
        mock_post.assert_not_called()
        mock_get.assert_not_called()

    @patch.object(dashboard.requests, "post")
    def test_invalid_identity_does_not_create_login(self, mock_post):
        state = self.start_login()
        mock_post.return_value = self.json_response(
            {"code": 0, "app_access_token": "access-token", "expire": time.time() + 3600}
        )
        with patch.object(dashboard.requests, "get") as mock_get:
            mock_get.return_value = self.json_response(
                {"code": 0, "employee": {"name": "Missing Email"}}
            )

            response = self.client.get(f"/?code=auth-code&state={state}")

        self.assertIn("seatalk_error=exchange", response.headers["Location"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)


if __name__ == "__main__":
    unittest.main()
