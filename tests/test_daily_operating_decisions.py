import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine


class DailyOperatingDecisionsTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(
            _ROOT, f".test_daily_operating_decisions_{self._testMethodName}.sqlite"
        )
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.addCleanup(self._cleanup_db)
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self.engine.state.today_conflict_event = {"status": "no_event"}
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = []
        for tent in self.engine.tents.values():
            tent.next_breakdown_turn = 999999
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine
    def _cleanup_db(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    def _submit_and_advance(self, actions):
        self.assertTrue(self.engine.submit_turn_plan([], actions)["success"])
        self.engine.advance_turn()

    def test_decisions_are_shared_across_turns_and_reset_on_new_day(self):
        self.assertEqual(self.engine.state.decisions_left, 5)
        self.engine.state.turn = 2

        self._submit_and_advance([
            {"action": "improve_service"},
            {"action": "clean_campsite"},
        ])
        self.assertEqual((self.engine.state.turn, self.engine.state.decisions_left), (3, 3))

        self._submit_and_advance([{"action": "improve_service"}])
        self.assertEqual((self.engine.state.turn, self.engine.state.decisions_left), (4, 2))

        self._submit_and_advance([
            {"action": "make_post"},
            {"action": "campfire"},
        ])
        self.assertEqual((self.engine.state.turn, self.engine.state.decisions_left), (5, 0))

        self.assertFalse(
            self.engine.submit_turn_plan([], [{"action": "stargazing"}])["success"]
        )
        self._submit_and_advance([])
        self.assertEqual((self.engine.state.turn, self.engine.state.decisions_left), (6, 0))

        self.assertTrue(self.engine.submit_day_end_actions([])["success"])
        self.assertTrue(self.engine.start_next_day()["success"])
        self.assertEqual((self.engine.state.day, self.engine.state.turn), (2, 1))
        self.assertEqual(self.engine.state.decisions_left, 5)

    def test_turn_plan_keeps_three_action_limit_and_repair_costs_one_point(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "broken"

        repair = self.engine.submit_turn_plan(
            [], [{"action": "repair_tent", "tent_id": 1}]
        )
        self.assertTrue(repair["success"])
        self.assertEqual(self.engine.state.decisions_left, 4)

        other = CampingPlazaEngine(db_path=":memory:")
        other.state.turn = 2
        too_many = other.submit_turn_plan([], [
            {"action": "improve_service"},
            {"action": "clean_campsite"},
            {"action": "make_post"},
            {"action": "buy_food_package", "package_key": "small"},
        ])
        self.assertFalse(too_many["success"])
        self.assertEqual(other.state.decisions_left, 5)

    def test_mcp_player_message_is_current_and_only_turn_one_has_rule_text(self):
        food_hint = "客人用餐会消耗食材，未使用的食材会在营业结束后作废。"

        # Day 1 Turn 1：首次进入经营状态，决策点规则后追加食材提示。
        morning = game_api.mcp_state()
        self.assertEqual(
            morning["player_message"],
            "今日经营决策点：5 / 5｜全天营业轮次共享｜当日未使用点数不结转。"
            "客人用餐会消耗食材，未使用的食材会在营业结束后作废。",
        )

        self.engine.state.turn = 2
        self.engine.state.decisions_left = 3
        state = game_api.mcp_state()
        actions = game_api.mcp_available_actions()
        self.assertEqual(state["player_message"], "今日经营决策点：3 / 5")
        self.assertNotIn(food_hint, state["player_message"])
        self.assertEqual(actions["player_message"], "今日经营决策点：3 / 5")
        self.assertNotIn(food_hint, actions["player_message"])
        self.assertEqual(actions["available_actions"][0]["max_decision_actions"], 3)

        result = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        self.assertEqual(
            result["events"][0],
            {"type": "operating_decisions", "text": "今日经营决策点：3 / 5"},
        )

        # Day 2 Turn 1：仍显示原有决策点规则，但不再重复食材提示。
        self.engine.state.day = 2
        self.engine.state.turn = 1
        self.engine.state.decisions_left = 5
        day_two_morning = game_api.mcp_state()
        self.assertEqual(
            day_two_morning["player_message"],
            "今日经营决策点：5 / 5｜全天营业轮次共享｜当日未使用点数不结转。",
        )
        self.assertNotIn(food_hint, day_two_morning["player_message"])

    def test_snapshot_preserves_current_remaining_decisions(self):
        self.engine.state.turn = 4
        self.engine.state.decisions_left = 2
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.load_state(), "loaded")
        self.assertEqual(restored.state.decisions_left, 2)

    def test_first_growth_phase_guidance_only_on_day_two_turn_six_entry(self):
        self.engine.state.day = 2
        self.engine.state.turn = 4
        day_two_turn_five = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        self.assertTrue(day_two_turn_five["success"])
        self.assertFalse(
            any(event.get("text") == "扩建与升级仅在 Turn 6 日终管理阶段进行。"
                for event in day_two_turn_five["events"])
        )
        self.assertEqual(self.engine.state.turn, 5)

        day_two_turn_six = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        self.assertTrue(day_two_turn_six["success"])
        self.assertEqual(self.engine.state.turn, 6)
        self.assertTrue(
            any(
                event.get("text") == "扩建与升级仅在 Turn 6 日终管理阶段进行。"
                for event in day_two_turn_six["events"]
            )
        )
        day_two_actions = game_api.mcp_available_actions()["available_actions"]

        self.engine.state.day = 3
        self.engine.state.turn = 4
        day_three_turn_five = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        self.assertTrue(day_three_turn_five["success"])
        self.assertFalse(
            any(event.get("text") == "扩建与升级仅在 Turn 6 日终管理阶段进行。"
                for event in day_three_turn_five["events"])
        )
        day_three_turn_six = game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        self.assertTrue(day_three_turn_six["success"])
        self.assertFalse(
            any(event.get("text") == "扩建与升级仅在 Turn 6 日终管理阶段进行。"
                for event in day_three_turn_six["events"])
        )
        day_three_actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual(
            [item["action"] for item in day_two_actions],
            [item["action"] for item in day_three_actions],
        )
