import sys
import unittest

sys.path.insert(0, "camping_plaza")

import game_api
from game_engine import CampingPlazaEngine


class McpTurnOutputCompactionTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine

    def test_execute_turn_plan_returns_history_delta_not_raw_events(self):
        self.engine.state.turn = 2
        self.engine.state.today_events = ["内部逐客组流水，不应公开"]

        result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[], actions=[]
        ))

        self.assertTrue(result["success"])
        self.assertEqual((result["executed_day"], result["executed_turn"]), (1, 2))
        self.assertEqual((result["day"], result["turn"]), (1, 3))
        self.assertIn("events", result)
        self.assertNotIn("内部逐客组流水", str(result["events"]))
        self.assertIn("action_results", result)
        self.assertIn("balance_delta", result)
        self.assertIn("income_delta", result)
        self.assertNotIn("action_failures", result)
        self.assertNotIn("tents", result)
        self.assertNotIn("npc_id", str(result))

    def test_execute_turn_plan_returns_compact_submitted_action_result(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "cleaning"

        result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[game_api.ActionRequest(
                action="clean_tents", params={"tent_ids": [1]}
            )],
            actions=[],
        ))

        self.assertEqual(result["action_results"], [{
            "action": "clean_tents", "success": True,
        }])
        self.assertNotEqual(self.engine.tents[1].status, "cleaning")

    def test_default_mcp_state_excludes_history_and_dashboard_fields(self):
        self.engine.state.turn = 3
        state = game_api.mcp_state()

        for field in (
            "arrival_plan", "event_history", "review_history", "today_events",
            "average_rating", "today_income", "greenery", "hot_spring",
            "day_campsite", "reservations", "turn_plan",
        ):
            self.assertNotIn(field, state)
        self.assertEqual(state["decisions_left"], 3)
        self.assertIn("food_stock", state)

    def test_mcp_state_does_not_keep_reservation_context(self):
        self.engine.state.reservations = [{
            "group_size": 3, "visit_type": "overnight", "arrival_day": 2,
            "status": "accepted", "tent_id": 1,
        }]

        state = game_api.mcp_state()

        self.assertNotIn("reservations", state)
        self.assertNotIn("confirmed_reservations", state)

    def test_mcp_actions_omits_fixed_query_menu_and_enabled_reasons(self):
        self.engine.state.turn = 2
        response = game_api.mcp_available_actions()
        entry = response["available_actions"][0]

        self.assertNotIn("available_queries", response)
        self.assertEqual(
            entry["description"],
            "每个 Turn 有 3 个决策点，不结转。本轮所有操作须一次提交：free_actions + 0～3 项 actions；提交即进入下一 Turn。成功后已进入下一 Turn，普通连续经营优先读取下一 Turn 的 /mcp/actions。",
        )
        self.assertEqual(response["food_stock"], self.engine.state.food_stock)
        self.assertNotIn("confirmed_reservations", response)
        self.assertNotIn("reservation_summary", response)
        for candidate in entry["decision_action_candidates"]:
            if candidate["enabled"]:
                self.assertNotIn("reason", candidate)


if __name__ == "__main__":
    unittest.main()
