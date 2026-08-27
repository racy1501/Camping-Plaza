import os
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class NatureObservationPhase2ATests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(
            tempfile.gettempdir(), "nature_observation_phase2a.sqlite"
        )
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self.engine.state.nature_observation_station_built = True
        self.engine.state.turn = 3

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def _prepare(self, result, *, npc_id=1, arrived=True):
        npc = NPCGroup(id=npc_id, group_size=2, visit_type="day")
        npc.has_left = not arrived
        self.engine.npc_pool.append(npc)
        entry = {
            "npc_id": npc_id,
            "planned_day": self.engine.state.day,
            "arrival_status": "arrived" if arrived else "turned_away_full",
            "observation_plan": {
                "planned_turn": self.engine.state.turn,
                "status": "pending",
                "result": result,
            },
        }
        self.engine.state.today_arrival_plan = [entry]
        return entry

    def test_completed_not_found_charges_once_and_records_event(self):
        history_before = len(self.engine.state.event_history)
        balance_before = self.engine.state.balance
        entry = self._prepare("not_found")
        turn_result = {"events": []}
        self.engine._process_nature_observation_plans(turn_result)
        self.assertEqual(entry["observation_plan"]["status"], "completed")
        self.assertEqual(self.engine.state.today_income["nature_observation"], 20)
        self.assertEqual(self.engine.state.balance, balance_before + 20)
        event = self.engine.state.event_history[-1]
        self.assertEqual(event["event_type"], "nature_observation")
        self.assertEqual(event["guest_ids"], [1])
        self.assertEqual(event["data"]["income"], 20)
        self.assertEqual(event["data"]["result"], "not_found")
        self.assertFalse(event["data"]["is_new_discovery"])
        self.assertEqual(len(turn_result["events"]), 1)
        self.assertIn("收入20金币", turn_result["events"][0])
        self.engine._process_nature_observation_plans({"events": []})
        self.assertEqual(self.engine.state.today_income["nature_observation"], 20)
        self.assertEqual(self.engine.state.balance, balance_before + 20)
        self.assertEqual(len(self.engine.state.event_history), history_before + 1)

    def test_completed_insect_charges_and_marks_new_or_repeat(self):
        balance_before = self.engine.state.balance
        self._prepare("ladybug")
        self.engine._process_nature_observation_plans({"events": []})
        event = self.engine.state.event_history[-1]
        self.assertTrue(event["data"]["is_new_discovery"])
        self.assertEqual(event["data"]["insect_name"], "七星瓢虫")
        self.assertEqual(self.engine.state.discovered_insects, ["ladybug"])

        self.engine.state.turn = 4
        self._prepare("ladybug", npc_id=2)
        self.engine._process_nature_observation_plans({"events": []})
        self.assertFalse(self.engine.state.event_history[-1]["data"]["is_new_discovery"])
        self.assertEqual(self.engine.state.discovered_insects, ["ladybug"])
        self.assertEqual(self.engine.state.today_income["nature_observation"], 40)
        self.assertEqual(self.engine.state.balance, balance_before + 40)

    def test_insect_collection_achievements_use_unique_discoveries_and_thresholds(self):
        insect_ids = [insect["id"] for insect in self.engine.INSECT_CATALOG]
        for index, insect_id in enumerate(insect_ids):
            self.engine.state.turn = index % 6 + 1
            self._prepare(insect_id, npc_id=index + 1)
            self.engine._process_nature_observation_plans({"events": []})
            unlocked = set(self.engine.state.unlocked_achievement_ids)
            if index == 0:
                self.assertIn("first_insect_discovered", unlocked)
                self.assertNotIn("insects_discovered_6", unlocked)
            if index == 4:
                self.assertNotIn("insects_discovered_6", unlocked)
            if index == 5:
                self.assertIn("insects_discovered_6", unlocked)
            if index == 10:
                self.assertNotIn("all_insects_discovered", unlocked)
            if index == 11:
                self.assertIn("all_insects_discovered", unlocked)

        self.assertEqual(len(self.engine.state.discovered_insects), 12)
        self.assertEqual(
            self.engine.state.pending_achievement_ids.count("first_insect_discovered"),
            1,
        )
        self.assertEqual(
            self.engine.state.pending_achievement_ids.count("insects_discovered_6"),
            1,
        )
        self.assertEqual(
            self.engine.state.pending_achievement_ids.count("all_insects_discovered"),
            1,
        )

    def test_repeated_insect_does_not_change_count_or_unlock_again(self):
        self._prepare("ladybug")
        self.engine._process_nature_observation_plans({"events": []})
        pending_before = list(self.engine.state.pending_achievement_ids)
        self.engine.state.turn = 4
        self._prepare("ladybug", npc_id=2)
        self.engine._process_nature_observation_plans({"events": []})
        self.assertEqual(self.engine.state.discovered_insects, ["ladybug"])
        self.assertEqual(self.engine.state.pending_achievement_ids, pending_before)

    def test_skipped_does_not_charge_or_publish_event(self):
        history_before = len(self.engine.state.event_history)
        balance_before = self.engine.state.balance
        entry = self._prepare("ladybug", arrived=False)
        self.engine._process_nature_observation_plans({"events": []})
        self.assertEqual(entry["observation_plan"]["status"], "skipped")
        self.assertEqual(self.engine.state.today_income["nature_observation"], 0)
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(len(self.engine.state.event_history), history_before)

    def test_station_day_end_log_says_build_not_upgrade(self):
        engine = CampingPlazaEngine(db_path=":memory:")
        engine.state.turn = 6
        engine.state.campsite_star = 3
        engine.state.balance = 800
        result = engine.submit_day_end_actions([{
            "action": "purchase_growth_project",
            "params": {"project_id": "nature_observation_station"},
        }])
        self.assertTrue(result["success"])
        history_texts = [event["text"] for event in engine.state.event_history]
        self.assertIn("日终完成：建设自然观察站，共支出800金币。", history_texts)
        self.assertNotIn("升级自然观察站", "\n".join(history_texts))
    def test_api_state_exposes_only_current_catalog_and_not_hidden_plan(self):
        self.engine.state.discovered_insects = ["ladybug"]
        self.engine.state.today_arrival_plan = [{
            "npc_id": 1,
            "observation_plan": {
                "planned_turn": 5,
                "status": "pending",
                "result": "stag_beetle",
            },
        }]
        full_state = self.engine.get_full_state()
        self.assertEqual(full_state["nature_observation"]["discovered_count"], 1)
        self.assertEqual(full_state["nature_observation"]["discovered_insects"][0]["id"], "ladybug")
        self.assertNotIn("today_arrival_plan", full_state)

    def test_mcp_state_is_compact_and_omits_hidden_plan_and_probability(self):
        previous_engine = game_api.engine
        try:
            game_api.engine = self.engine
            self.engine.state.player_name = "测试"
            self.engine.state.discovered_insects = ["ladybug"]
            self.engine.state.today_arrival_plan = [{
                "observation_plan": {
                    "planned_turn": 5,
                    "status": "pending",
                    "result": "stag_beetle",
                }
            }]
            state = game_api.mcp_state()
            nature = state["nature_observation"]
            self.assertEqual(nature["discovered_count"], 1)
            self.assertEqual(nature["discovered_insects"], ["ladybug"])
            self.assertNotIn("planned_turn", str(state))
            self.assertNotIn("stag_beetle", nature["discovered_insects"])
            self.assertNotIn("probability", str(state))
        finally:
            game_api.engine = previous_engine


if __name__ == "__main__":
    unittest.main()
