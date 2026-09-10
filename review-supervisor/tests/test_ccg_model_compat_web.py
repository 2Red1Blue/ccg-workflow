from __future__ import annotations

import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ccg_review_web import ReviewServer


class ModelCompatibilityWebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.server = ReviewServer(root, port=0, compatibility_path=root / "model-compatibility.json")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def get_rules(self):
        code, _headers, data = self.request("GET", "/api/model-compat")
        self.assertEqual(code, 200)
        self.assertEqual(data["config"], {"version": 1, "rules": []})
        self.assertTrue(data["revision"])
        self.assertTrue(data["csrf_token"])
        return data

    def save_headers(self, state, **extra):
        headers = {
            "Origin": self.server.origin,
            "Content-Type": "application/json",
            "X-CCG-Request": "model-compat",
            "X-CCG-CSRF": state["csrf_token"],
            "If-Match": state["revision"],
        }
        headers.update(extra)
        return headers

    def test_default_save_and_reload_exact_rule(self):
        state = self.get_rules()
        config = {"version": 1, "rules": [{"provider_id": "zhipu", "model": "glm-5-3-flash", "omit_disabled_thinking": True}]}
        code, _headers, saved = self.request("PUT", "/api/model-compat", json.dumps(config), self.save_headers(state))
        self.assertEqual(code, 200)
        self.assertEqual(saved["config"], config)
        self.assertNotEqual(saved["revision"], state["revision"])
        second = {"version": 1, "rules": []}
        second_state = {**saved, "csrf_token": state["csrf_token"]}
        code, _headers, saved_again = self.request("PUT", "/api/model-compat", json.dumps(second), self.save_headers(second_state))
        self.assertEqual(code, 200)
        self.assertEqual(saved_again["config"], second)
        code, _headers, loaded = self.request("GET", "/api/model-compat")
        self.assertEqual(code, 200)
        self.assertEqual(loaded["config"], second)
        self.assertEqual(loaded["revision"], saved_again["revision"])

    def test_conflict_requires_reload(self):
        state = self.get_rules()
        config = {"version": 1, "rules": [{"provider_id": "zhipu", "model": "glm-5-3-flash", "omit_disabled_thinking": True}]}
        self.assertEqual(self.request("PUT", "/api/model-compat", json.dumps(config), self.save_headers(state))[0], 200)
        self.assertEqual(self.request("PUT", "/api/model-compat", json.dumps(config), self.save_headers(state))[0], 409)

    def test_invalid_policy_load_has_safe_recovery_details(self):
        self.server.compatibility.path.write_text("{")
        self.server.compatibility.path.chmod(0o600)
        code, _headers, data = self.request("GET", "/api/model-compat")
        self.assertEqual(code, 400)
        self.assertEqual(data["path"], str(self.server.compatibility.path))
        self.assertEqual(data["recovery"], "Check the JSON syntax and mode 0600, then reload the rules.")
        self.assertEqual(data["error"], "Compatibility configuration unavailable")

    def test_invalid_schema_put_returns_safe_validation_detail(self):
        state = self.get_rules()
        code, _headers, data = self.request("PUT", "/api/model-compat", json.dumps({"version": 2, "rules": []}), self.save_headers(state))
        self.assertEqual(code, 400)
        self.assertEqual(data, {"error": "version must be exactly integer 1"})

    def test_put_requires_exact_same_origin_and_request_headers(self):
        state = self.get_rules()
        body = json.dumps({"version": 1, "rules": []})
        for headers in (
            self.save_headers(state, Origin="https://attacker.example"),
            self.save_headers(state, Host="attacker.example"),
            self.save_headers(state, **{"X-CCG-Request": "other"}),
            self.save_headers(state, **{"X-CCG-CSRF": "wrong"}),
        ):
            self.assertEqual(self.request("PUT", "/api/model-compat", body, headers)[0], 403)
        headers = self.save_headers(state)
        headers.pop("Origin")
        self.assertEqual(self.request("PUT", "/api/model-compat", body, headers)[0], 403)

    def test_put_rejects_malformed_and_ambiguous_bodies(self):
        state = self.get_rules()
        self.assertEqual(self.request("PUT", "/api/model-compat", "{", self.save_headers(state))[0], 400)
        self.assertEqual(self.request("PUT", "/api/model-compat", "{}", self.save_headers(state, **{"Content-Type": "text/plain"}))[0], 415)
        self.assertEqual(self.request("PUT", "/api/model-compat", "{}", self.save_headers(state, **{"Transfer-Encoding": "chunked"}))[0], 400)
        self.assertEqual(self.request("PUT", "/api/model-compat", "x" * (64 * 1024 + 1), self.save_headers(state))[0], 413)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.putrequest("PUT", "/api/model-compat")
            for key, value in self.save_headers(state).items():
                connection.putheader(key, value)
            connection.endheaders()
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()

    def test_half_closed_short_body_is_rejected_without_saving(self):
        state = self.get_rules()
        headers = self.save_headers(state)
        request = [
            "PUT /api/model-compat HTTP/1.1",
            f"Host: {self.server.origin.removeprefix('http://')}",
            *(f"{key}: {value}" for key, value in headers.items()),
            "Content-Length: 3",
            "",
            "{}",
        ]
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=2) as client:
            client.sendall("\r\n".join(request).encode())
            client.shutdown(socket.SHUT_WR)
            response = client.recv(4096)
        self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
        self.assertEqual(self.get_rules()["config"], {"version": 1, "rules": []})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.putrequest("PUT", "/api/model-compat")
            for key, value in self.save_headers(state).items():
                connection.putheader(key, value)
            connection.putheader("Content-Length", "2")
            connection.putheader("Content-Length", "2")
            connection.endheaders(b"{}")
            self.assertEqual(connection.getresponse().status, 400)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
