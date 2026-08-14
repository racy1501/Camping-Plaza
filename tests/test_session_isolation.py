"""多 session 存档隔离的 SQLite API 定向测试。"""

import os
import sqlite3
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api


class SessionIsolationTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            dir=os.path.join(_PROJECT_ROOT, "tests"),
            prefix="session_isolation_",
            suffix=".db",
            delete=False,
        )
        self.db_path = handle.name
        handle.close()
        self.original_db_path = game_api.DB_PATH
        self.original_engine = game_api.engine
        game_api.DB_PATH = self.db_path
        game_api.engine = None
        self.client = TestClient(game_api.app)

    def tearDown(self):
        self.client.close()
        game_api.DB_PATH = self.original_db_path
        game_api.engine = self.original_engine
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except PermissionError:
                pass

    def _create_session(self):
        response = self.client.post("/api/session")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["success"])
        return payload

    def _state(self, session_id):
        response = self.client.get("/api/state", params={"session_id": session_id})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_sessions_are_isolated_and_restore_from_sqlite(self):
        created_a = self._create_session()
        created_b = self._create_session()
        session_a = created_a["session_id"]
        session_b = created_b["session_id"]
        self.assertNotEqual(session_a, session_b)
        self.assertEqual(created_a["state"]["day"], created_b["state"]["day"])
        self.assertEqual(created_a["state"]["balance"], created_b["state"]["balance"])

        advanced_a = self.client.post("/api/turn/advance", json={"session_id": session_a})
        self.assertEqual(advanced_a.status_code, 200, advanced_a.text)
        state_a_after_advance = self._state(session_a)
        self.assertEqual(state_a_after_advance["turn"], 2)
        self.assertEqual(self._state(session_b)["turn"], 1)

        advanced_b = self.client.post(
            "/api/action",
            json={"session_id": session_b, "action": "advance_turn"},
        )
        self.assertEqual(advanced_b.status_code, 200)
        self.assertEqual(self._state(session_b)["turn"], 2)
        self.assertEqual(self._state(session_a)["turn"], 2)

        # 每个请求均重新按 session 加载引擎，模拟 Render 进程重启后的恢复。
        game_api.engine = None
        restored_a = self._state(session_a)
        restored_b = self._state(session_b)
        self.assertEqual(restored_a["turn"], 2)
        self.assertEqual(restored_b["turn"], 2)

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT session_id FROM runtime_snapshot ORDER BY session_id"
            ).fetchall()
        self.assertEqual({row[0] for row in rows}, {session_a, session_b})

    def test_stateful_requests_require_existing_session_id(self):
        missing = self.client.get("/api/state")
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(missing.json()["detail"]["error_code"], "missing_session_id")

        unknown = self.client.get(
            "/mcp/state",
            params={"session_id": "sess_" + "f" * 32},
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["detail"]["error_code"], "session_not_found")


if __name__ == "__main__":
    unittest.main()
