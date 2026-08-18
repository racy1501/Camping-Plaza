import copy
import sys
import unittest
from unittest import mock

sys.path.insert(0, "camping_plaza")

import game_api
from game_engine import CampingPlazaEngine


class McpActionCatalogTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine

    def _submit_entry(self):
        actions = game_api.mcp_available_actions()["available_actions"]
        return next(item for item in actions if item["action"] == "execute_turn_plan")

    def test_turn2_candidates_include_core_plan_actions_and_clean_tents(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "cleaning"
        entry = self._submit_entry()
        free = {item["action"]: item for item in entry["free_action_candidates"]}
        decision = {item["action"]: item for item in entry["decision_action_candidates"]}
        self.assertEqual(free["clean_tents"]["params"], {"tent_ids": [1]})
        self.assertEqual(free["clean_tents"]["cost_decision_points"], 0)
        for name in ("improve_service", "clean_campsite", "make_post"):
            self.assertTrue(decision[name]["enabled"])
            self.assertEqual(decision[name]["cost_decision_points"], 1)
            self.assertEqual(decision[name]["params"], {})

    def test_turn_plan_action_name_and_endpoint_match_execution_semantics(self):
        self.engine.state.turn = 2
        entry = self._submit_entry()
        self.assertEqual(entry["action"], "execute_turn_plan")
        self.assertEqual(entry["endpoint"], "/api/turn/plan")
        description = entry["description"]
        self.assertNotIn("每个 Turn 有 3 个决策点，不结转。", description)
        self.assertIn("本轮所有操作须一次提交", description)
        self.assertIn("free_actions", description)
        self.assertIn("0～3 项 actions", description)
        self.assertIn("提交即进入下一 Turn", description)
        self.assertIn("普通连续经营优先读取下一 Turn 的 /mcp/actions", description)

    def test_turn_specific_candidates_and_daily_limits(self):
        self.engine.state.turn = 3
        self.assertNotIn("campfire", {x["action"] for x in self._submit_entry()["decision_action_candidates"]})
        self.engine.state.turn = 4
        self.assertIn("campfire", {x["action"] for x in self._submit_entry()["decision_action_candidates"]})
        self.engine.state.turn = 5
        self.assertIn("stargazing", {x["action"] for x in self._submit_entry()["decision_action_candidates"]})
        self.engine.state.improve_service_uses_today = 2
        self.engine.state.clean_campsite_uses_today = 2
        self.engine.state.post_used_today = True
        candidates = {x["action"]: x for x in self._submit_entry()["decision_action_candidates"]}
        self.assertFalse(candidates["improve_service"]["enabled"])
        self.assertEqual(candidates["improve_service"]["remaining_today"], 0)
        self.assertFalse(candidates["clean_campsite"]["enabled"])
        self.assertEqual(candidates["clean_campsite"]["remaining_today"], 0)
        self.assertFalse(candidates["make_post"]["enabled"])
        self.assertEqual(candidates["make_post"]["remaining_today"], 0)
        self.assertTrue(candidates["improve_service"]["reason"])

    def test_clean_tents_is_omitted_without_cleaning_tents(self):
        self.engine.state.turn = 2
        self.assertEqual(self._submit_entry()["free_action_candidates"], [])

    def test_conflict_is_immediate_and_exposes_required_choice_enum(self):
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3, "verbal_result": {}, "gift_result": {},
        }
        before = copy.deepcopy(self.engine.state)
        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual([item["action"] for item in actions], ["resolve_temporary_conflict"])
        self.assertEqual(actions[0]["choices"], ["verbal", "gift"])
        self.assertIsNone(actions[0]["params"]["choice"])
        self.assertEqual(actions[0]["required_params"][0]["enum"], ["verbal", "gift"])
        self.assertEqual(self.engine.state, before)

    def test_blocked_plan_returns_conflict_resolution_guidance(self):
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }

        result = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "temporary_conflict_pending")
        next_action = result["next_action"]
        self.assertEqual(next_action["action"], "resolve_temporary_conflict")
        self.assertEqual(next_action["params"], {"choice": None})
        self.assertEqual(next_action["choices"], ["verbal", "gift"])

        self.assertTrue(self.engine.resolve_current_temporary_conflict("verbal")["success"])
        resumed = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        self.assertTrue(resumed["success"])

    def test_submitted_plan_only_exposes_advance(self):
        self.engine.state.turn = 3
        self.engine.state.pending_turn_plan = {
            "target_day": self.engine.state.day, "target_turn": 3,
            "free_actions": [], "actions": [],
        }
        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual([item["action"] for item in actions], ["advance_turn"])

    def test_human_and_mcp_candidate_availability_matches(self):
        for turn in (2, 3, 4, 5):
            self.engine.state.turn = turn
            self.engine.state.pending_turn_plan = None
            mcp = self._submit_entry()
            human = game_api.get_human_actions()
            human_candidates = human["free_action_candidates"] + human["decision_action_candidates"]
            mcp_candidates = mcp["free_action_candidates"] + mcp["decision_action_candidates"]
            human_by_action = {item["action"]: item for item in human_candidates}
            mcp_by_action = {item["action"]: item for item in mcp_candidates}
            self.assertTrue(set(mcp_by_action).issubset(set(human_by_action)))
            for action in mcp_by_action:
                self.assertEqual(human_by_action[action]["enabled"], mcp_by_action[action]["enabled"])
                if not mcp_by_action[action]["enabled"]:
                    self.assertEqual(human_by_action[action]["reason"], mcp_by_action[action]["reason"])
                else:
                    self.assertNotIn("reason", mcp_by_action[action])

    def test_mcp_adapter_does_not_depend_on_human_catalog(self):
        self.engine.state.turn = 2
        with mock.patch.object(
            game_api,
            "_build_human_action_catalog",
            side_effect=AssertionError("MCP must use the neutral source"),
        ):
            candidates = game_api._build_turn_action_candidates(self.engine)
        self.assertEqual(candidates["free_action_candidates"], [])
        self.assertTrue(candidates["decision_action_candidates"])


if __name__ == "__main__":
    unittest.main()
