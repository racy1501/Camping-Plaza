import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class NatureObservationPhase2BTests(unittest.TestCase):
    def setUp(self):
        db_dir = os.path.join(tempfile.gettempdir(), "camping_plaza_fix_temp")
        os.makedirs(db_dir, exist_ok=True)
        self.db_path = os.path.join(
            db_dir, f"nature_observation_phase2b_{uuid.uuid4().hex}.sqlite"
        )
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self.original_engine = game_api.engine
        game_api.engine = self.engine
        self.addCleanup(setattr, game_api, "engine", self.original_engine)

    def _turn6_three_star(self, *, balance=800):
        self.engine.state.player_name = "测试"
        self.engine.state.turn = 6
        self.engine.state.campsite_star = 3
        self.engine.state.balance = balance

    def _complete_observation(self, result, *, npc_id=1):
        self.engine.state.nature_observation_station_built = True
        self.engine.state.turn = 3
        self.engine.npc_pool.append(NPCGroup(id=npc_id, group_size=2, visit_type="day"))
        self.engine.state.today_arrival_plan = [{
            "npc_id": npc_id,
            "planned_day": self.engine.state.day,
            "arrival_status": "arrived",
            "observation_plan": {
                "planned_turn": 3,
                "status": "pending",
                "result": result,
            },
        }]
        self.engine._process_nature_observation_plans({"events": []})
        return self.engine.state.event_history[-1]

    def test_intro_is_only_human_turn6_payload_and_acknowledgement_persists(self):
        self.engine.state.turn = 6
        self.engine.state.campsite_star = 2
        self.assertNotIn("nature_observation_intro", game_api.get_human_actions())
        unavailable = game_api.acknowledge_nature_observation_intro(
            game_api.SessionRequest()
        )
        self.assertFalse(unavailable["success"])
        self.assertFalse(self.engine.state.nature_observation_intro_seen)

        self._turn6_three_star(balance=799)
        actions = game_api.get_human_actions()
        self.assertIn("nature_observation_intro", actions)
        station = next(item for item in actions["day_end_action_candidates"]
                       if item["params"].get("project_id") == "nature_observation_station")
        self.assertFalse(station["enabled"])
        self.assertEqual(station["reason"], "金币不足")
        self.assertFalse(self.engine.state.nature_observation_intro_seen)

        acknowledged = game_api.acknowledge_nature_observation_intro(
            game_api.SessionRequest()
        )
        self.assertTrue(acknowledged["success"])
        self.assertTrue(self.engine.state.nature_observation_intro_seen)
        self.assertNotIn("nature_observation_intro", game_api.get_human_actions())

        restored = CampingPlazaEngine(db_path=self.engine.db_path)
        self.assertTrue(restored.state.nature_observation_intro_seen)

    def test_old_snapshot_defaults_intro_seen_to_false(self):
        self.assertTrue(self.engine.save_state())
        with sqlite3.connect(self.engine.db_path) as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = ?",
                (self.engine.session_id,),
            ).fetchone()
        payload = json.loads(row[0])
        payload["state"].pop("nature_observation_intro_seen", None)
        with sqlite3.connect(self.engine.db_path) as conn:
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ? WHERE session_id = ?",
                (json.dumps(payload, ensure_ascii=False), self.engine.session_id),
            )
            conn.commit()
        restored = CampingPlazaEngine(db_path=self.engine.db_path)
        self.assertFalse(restored.state.nature_observation_intro_seen)

    def test_station_build_feedback_only_on_success(self):
        self._turn6_three_star()
        success = self.engine.purchase_growth_project("nature_observation_station")
        self.assertTrue(success["success"])
        self.assertEqual(
            success["message"],
            "自然观察站建成了，从明天开始客人将有机会参加自然观察活动。",
        )
        repeated = self.engine.purchase_growth_project("nature_observation_station")
        self.assertFalse(repeated["success"])
        self.assertNotIn("message", repeated)

    def test_discovery_crossing_threshold_records_one_non_probability_hint_fact(self):
        self.engine.state.discovered_insects = ["ladybug", "white_butterfly"]
        event = self._complete_observation("dragonfly")
        data = event["data"]
        self.assertTrue(data["is_new_discovery"])
        self.assertTrue(data["observation_ability_unlocked"])
        self.assertNotIn("percent", str(data))

        repeat = self._complete_observation("dragonfly", npc_id=2)
        self.assertFalse(repeat["data"]["is_new_discovery"])
        self.assertNotIn("observation_ability_unlocked", repeat["data"])

    def test_public_catalog_has_fixed_known_slots_without_undiscovered_names(self):
        self.engine.state.discovered_insects = ["ladybug", "stag_beetle"]
        catalog = self.engine.get_full_state()["nature_observation"]
        self.assertEqual(catalog["discovered_count"], 2)
        self.assertEqual(catalog["total_count"], 12)
        self.assertEqual(
            [(item["id"], item["catalog_index"]) for item in catalog["discovered_insects"]],
            [("ladybug", 1), ("stag_beetle", 12)],
        )
        public_text = str(catalog)
        self.assertNotIn("粉蝶", public_text)
        self.assertNotIn("white_butterfly", public_text)

    def test_pending_observation_remains_hidden_from_public_api_and_mcp_state(self):
        self.engine.state.player_name = "测试"
        self.engine.state.today_arrival_plan = [{
            "npc_id": 7,
            "observation_plan": {
                "planned_turn": 5,
                "status": "pending",
                "result": "stag_beetle",
            },
        }]
        api_state = game_api.get_state()
        mcp_state = game_api.mcp_state()
        self.assertNotIn("today_arrival_plan", api_state)
        self.assertNotIn("planned_turn", str(api_state))
        self.assertNotIn("stag_beetle", str(api_state))
        self.assertNotIn("planned_turn", str(mcp_state))
        self.assertNotIn("stag_beetle", str(mcp_state))

    def test_completed_observation_is_public_as_an_event_fact(self):
        event = self._complete_observation("ladybug")
        public_events = game_api.get_state()["event_history"]
        self.assertEqual(public_events[-1]["sequence"], event["sequence"])
        self.assertEqual(public_events[-1]["event_type"], "nature_observation")
        self.assertEqual(public_events[-1]["data"]["insect_name"], "七星瓢虫")


class NatureObservationPhase2BFrontendTests(unittest.TestCase):
    def test_frontend_uses_public_facts_for_catalog_and_turn_merge_only(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "camping_plaza" / "frontend" / "scripts" / "overview.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("function mergeNatureObservationEvents(events)", source)
        self.assertIn("function natureObservationText(events)", source)
        self.assertIn("function renderInsectCatalogState(state)", source)
        self.assertIn("catalog_index", source)
        self.assertIn("？？？", source)
        self.assertIn("observation_ability_unlocked", source)
        self.assertNotIn("observation_plan", source)
        self.assertNotIn("planned_turn", source)


if __name__ == "__main__":
    unittest.main()
