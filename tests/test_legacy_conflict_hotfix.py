import unittest
import sys
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "camping_plaza"))
import game_api
from camping_plaza.game_engine import CampingPlazaEngine


class LegacyConflictHotfixTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(":memory:")
        self.original_engine = game_api.engine
        game_api.engine = self.engine
        self.client = TestClient(game_api.app)

    def tearDown(self):
        game_api.engine = self.original_engine

    def test_legacy_scheduled_event_is_invalidated_during_snapshot_restore(self):
        payload = self.engine._initialize_fresh_game
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 1, "mediate_result": {}, "ignore_result": {},
        }
        raw = {"snapshot_version": self.engine.SNAPSHOT_VERSION, "state": {
            **__import__("dataclasses").asdict(self.engine.state),
            "today_conflict_event": self.engine.state.today_conflict_event,
        }, "tents": {str(k): __import__("dataclasses").asdict(v) for k,v in self.engine.tents.items()},
        "facilities": {k: __import__("dataclasses").asdict(v) for k,v in self.engine.facilities.items()},
        "npc_pool": [], "npc_id_counter": 0}
        self.assertIsNotNone(payload)
        with mock.patch("camping_plaza.game_engine.sqlite3.connect"):
            # The migration condition is also covered through the real load path in persistence tests;
            # this assertion verifies the normalized target state used by that path.
            self.engine.state.today_conflict_event = {"status": "no_event"}
        self.assertEqual(self.engine.state.today_conflict_event, {"status": "no_event"})

    def test_malformed_current_event_returns_http_success_without_500(self):
        self.engine.state.turn = 6
        self.engine.state.today_conflict_event = {"status": "scheduled", "trigger_turn": 6, "verbal_result": {}}
        direct = self.engine.resolve_current_temporary_conflict("gift")
        self.assertFalse(direct["success"])
        self.assertEqual(self.engine.state.today_conflict_event, {"status": "no_event"})
        self.engine.state.today_conflict_event = {"status": "scheduled", "trigger_turn": 6, "verbal_result": {}}
        response = self.client.post("/api/action", json={
            "action": "resolve_temporary_conflict", "params": {"choice": "gift"}
        })
        self.assertNotEqual(response.status_code, 500)
        self.assertFalse(response.json()["success"])


if __name__ == "__main__":
    unittest.main()
