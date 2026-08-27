"""首次进入欢迎层与经营者名称的 API 定向测试。"""

import os
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api


class PlayerNameOnboardingTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            dir=os.path.join(_PROJECT_ROOT, "tests"), suffix=".db", delete=False,
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
        payload = self.client.post("/api/session").json()
        self.assertTrue(payload["success"])
        return payload["session_id"]

    def _mcp_state(self, session_id):
        return self.client.get("/mcp/state", params={"session_id": session_id})

    def _set_name(self, session_id, name):
        return self.client.post(
            "/api/player/name", json={"session_id": session_id, "name": name},
        )

    def test_new_session_only_exposes_onboarding_and_name_action(self):
        session_id = self._create_session()
        state = self._mcp_state(session_id)
        self.assertEqual(state.status_code, 200)
        self.assertEqual(set(state.json()), {"onboarding"})
        onboarding = state.json()["onboarding"]
        self.assertEqual(onboarding["game"], "露营广场")
        self.assertIn("21,000", onboarding["message"])
        self.assertIn("Day 26 晨间将统一结算", onboarding["message"])
        self.assertNotIn("第 25 天结束前还清", onboarding["message"])
        self.assertIn("2-3", onboarding["name_rules"])

        actions = self.client.get("/mcp/actions", params={"session_id": session_id})
        self.assertEqual(actions.status_code, 200)
        catalog = actions.json()
        self.assertEqual([item["action"] for item in catalog["available_actions"]], ["set_player_name"])
        self.assertEqual(catalog["available_actions"][0]["endpoint"], "/api/player/name")
        self.assertNotIn("available_queries", catalog)

        blocked = self.client.post("/api/turn/advance", json={"session_id": session_id})
        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(blocked.json()["detail"]["error_code"], "onboarding_required")

    def test_valid_names_enter_day_one_without_advancing_turn(self):
        for name in ("小明", "露营者", "A1", "Camp99"):
            with self.subTest(name=name):
                session_id = self._create_session()
                response = self._set_name(session_id, name)
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(payload["message"], f"欢迎你，{name}。")
                self.assertEqual(payload["state"]["day"], 1)
                self.assertEqual(payload["state"]["turn"], 1)
                self.assertNotIn("onboarding", payload["state"])

                restored = self._mcp_state(session_id).json()
                self.assertEqual(restored["day"], 1)
                self.assertEqual(restored["turn"], 1)
                normal_actions = self.client.get("/mcp/actions", params={"session_id": session_id}).json()
                self.assertNotIn("set_player_name", [item["action"] for item in normal_actions["available_actions"]])

    def test_invalid_name_is_rejected_without_changing_state(self):
        for name in ("露", "露营广场", "abcdefg", "A B", "A_B", "A!", "😀😀", "露营A"):
            with self.subTest(name=name):
                session_id = self._create_session()
                response = self._set_name(session_id, name)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["detail"]["error_code"], "invalid_player_name")
                self.assertEqual(set(self._mcp_state(session_id).json()), {"onboarding"})

    def test_name_is_persisted_and_cannot_be_changed(self):
        session_id = self._create_session()
        self.assertEqual(self._set_name(session_id, "露营者").status_code, 200)
        game_api.engine = None
        self.assertEqual(self._mcp_state(session_id).json()["day"], 1)
        repeat = self._set_name(session_id, "新名字")
        self.assertEqual(repeat.status_code, 400)
        self.assertEqual(repeat.json()["detail"]["error_code"], "player_name_already_set")

    def test_session_names_are_isolated(self):
        session_a = self._create_session()
        session_b = self._create_session()
        self.assertEqual(self._set_name(session_a, "小明").status_code, 200)
        self.assertEqual(set(self._mcp_state(session_b).json()), {"onboarding"})


if __name__ == "__main__":
    unittest.main()
