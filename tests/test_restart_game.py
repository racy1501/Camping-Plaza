"""AI 原地重新开始游戏的定向测试。"""

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import NPCGroup


class RestartGameTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            dir=os.path.join(_PROJECT_ROOT, "tests"),
            prefix="restart_game_",
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

    def _create_named_session(self):
        session_id = self.client.post("/api/session").json()["session_id"]
        response = self.client.post(
            "/api/player/name",
            json={"session_id": session_id, "name": "露营者"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return session_id

    def _actions(self, session_id):
        return self.client.get("/mcp/actions", params={"session_id": session_id}).json()

    def test_restart_is_hidden_before_onboarding_and_visible_after_name(self):
        session_id = self.client.post("/api/session").json()["session_id"]
        actions = self._actions(session_id)
        self.assertNotIn("restart_game", [item["action"] for item in actions["available_actions"]])

        self.client.post(
            "/api/player/name",
            json={"session_id": session_id, "name": "露营者"},
        )
        actions = self._actions(session_id)
        restart = next(item for item in actions["available_actions"] if item["action"] == "restart_game")
        self.assertEqual(restart["description"], "重新开始当前游戏，需二次确认。")
        self.assertEqual(restart["params"], {"confirm": ""})

    def test_restart_requires_exact_confirmation_without_mutating_state(self):
        session_id = self._create_named_session()
        engine = game_api.get_engine(session_id)
        engine.state.day = 4
        engine.state.turn = 3
        engine.state.balance = 777
        engine.state.event_history.append({"text": "旧进度", "event_type": "legacy"})
        self.assertTrue(engine.save_state())
        before = copy.deepcopy(self.client.get("/api/state", params={"session_id": session_id}).json())

        for payload in (
            {"session_id": session_id, "action": "restart_game"},
            {"session_id": session_id, "action": "restart_game", "params": {"confirm": "确认"}},
        ):
            response = self.client.post("/api/action", json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertFalse(result["success"])
            self.assertTrue(result["confirmation_required"])
            self.assertEqual(
                self.client.get("/api/state", params={"session_id": session_id}).json(),
                before,
            )

    def test_exact_confirmation_restarts_in_place_and_persists(self):
        session_id = self._create_named_session()
        engine = game_api.get_engine(session_id)
        engine.state.day = 10
        engine.state.turn = 6
        engine.state.balance = 4321
        engine.state.debt_remaining = 123
        engine.state.food_stock = 99
        engine.state.total_reviews = 8
        engine.state.total_served_groups = 20
        engine.state.unlocked_achievement_ids = ["served_groups_10"]
        engine.state.reservations = [{"status": "accepted", "arrival_day": 11}]
        engine.state.review_history = [{"rating": 5}]
        engine.state.event_history.append({"text": "旧进度", "event_type": "legacy"})
        engine.npc_pool.append(NPCGroup(id=999, group_size=2, visit_type="day"))
        engine.tents[1].status = "broken"
        engine.facilities["entertainment"].level = 2
        engine.state.decisions_left = 1
        self.assertTrue(engine.save_state())

        before_session = engine.session_id
        before_decisions = engine.state.decisions_left
        response = self.client.post("/api/action", json={
            "session_id": session_id,
            "action": "restart_game",
            "params": {"confirm": "确认重新开始"},
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(
            result,
            {
                "success": True,
                "restarted": True,
                "day": 1,
                "turn": 1,
                "message": "游戏已重新开始。",
            },
        )
        self.assertEqual(engine.session_id, before_session)
        self.assertEqual(engine.state.player_name, "露营者")
        self.assertEqual(engine.state.decisions_left, before_decisions)
        self.assertIsNone(engine.state.pending_turn_plan)

        restored = self.client.get("/api/state", params={"session_id": session_id}).json()
        self.assertEqual(restored["player_name"], "露营者")
        self.assertEqual((restored["day"], restored["turn"]), (1, 1))
        self.assertEqual(restored["balance"], 1000)
        self.assertEqual(restored["debt_remaining"], 21000)
        self.assertEqual(restored["food_stock"], 8)
        self.assertEqual(restored["reservations"], [])
        self.assertEqual(restored["review_history"], [])
        self.assertNotIn("旧进度", str(restored["event_history"]))
        self.assertEqual(restored["tents"]["1"]["status"], "available")
        self.assertEqual(restored["facilities"]["entertainment"]["level"], 0)

        game_api.engine = None
        reloaded = self.client.get("/api/state", params={"session_id": session_id}).json()
        self.assertEqual((reloaded["day"], reloaded["turn"]), (1, 1))
        self.assertEqual(reloaded["player_name"], "露营者")
        reloaded_engine = game_api.get_engine(session_id)
        self.assertEqual(reloaded_engine.npc_pool, [])
        self.assertEqual(reloaded_engine._npc_id_counter, len(reloaded_engine.state.today_arrival_plan))
        self.assertEqual(reloaded_engine.state.today_arrival_plan_day, 1)
        self.assertEqual(reloaded_engine.state.total_reviews, 0)
        self.assertEqual(reloaded_engine.state.total_served_groups, 0)
        self.assertEqual(reloaded_engine.state.unlocked_achievement_ids, [])
        self.assertEqual(reloaded_engine.state.pending_reviews, [])

        actions = self._actions(session_id)
        self.assertIn("restart_game", [item["action"] for item in actions["available_actions"]])
        self.assertNotIn(
            "restart_game",
            [item["action"] for item in actions["available_actions"][0].get("day_end_action_candidates", [])],
        )

    def test_restart_is_not_a_turn_plan_action_or_day_end_candidate(self):
        session_id = self._create_named_session()
        engine = game_api.get_engine(session_id)
        self.assertNotIn("restart_game", engine.TURN_PLAN_ACTIONS)
        engine.state.turn = 6
        engine.state.day_end_completed = False
        self.assertTrue(engine.save_state())
        actions = self._actions(session_id)
        restart = [item for item in actions["available_actions"] if item["action"] == "restart_game"]
        self.assertEqual(len(restart), 1)
        self.assertNotIn(
            "restart_game",
            [item["action"] for item in actions["available_actions"][0]["day_end_action_candidates"]],
        )

    def test_frontend_has_no_restart_entry(self):
        frontend = Path(_PROJECT_ROOT) / "camping_plaza" / "frontend"
        for path in (frontend / "index.html", frontend / "scripts" / "overview.js"):
            self.assertNotIn("restart_game", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
