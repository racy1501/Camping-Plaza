"""启动负债 API / MCP 链路定向测试。"""

import sys
import unittest
from unittest import mock

sys.path.insert(0, "camping_plaza")

import game_api
from game_engine import CampingPlazaEngine


class DebtApiMcpTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine

    def _action_names(self):
        return [item["action"] for item in game_api.mcp_available_actions()["available_actions"]]

    def test_repay_is_hidden_before_turn6(self):
        for turn in (1, 2, 3, 4, 5):
            with self.subTest(turn=turn):
                self.engine.state.turn = turn
                self.engine.state.day_end_completed = False
                self.assertNotIn("repay_debt", self._action_names())

    def test_turn6_exposes_repay_alongside_day_end_action(self):
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False

        actions = game_api.mcp_available_actions()["available_actions"]
        names = [item["action"] for item in actions]

        self.assertIn("repay_debt", names)
        self.assertIn("submit_day_end_actions", names)
        repay = next(item for item in actions if item["action"] == "repay_debt")
        self.assertEqual(repay["params"], {"amount": None})
        self.assertEqual(repay["required_params"][0]["name"], "amount")
        self.assertIn("不占经营决策位", repay["description"])

    def test_completed_day_end_hides_repay(self):
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True
        self.assertNotIn("repay_debt", self._action_names())

    def test_api_rejects_repayment_outside_turn6(self):
        self.engine.state.turn = 5
        with self.assertRaises(game_api.HTTPException) as context:
            game_api.do_action(game_api.ActionRequest(
                action="repay_debt", params={"amount": 100}
            ))
        self.assertEqual(context.exception.detail["error_code"], "repayment_turn_not_allowed")

    def test_api_repayment_uses_engine_validation_and_updates_state(self):
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False
        self.engine.state.balance = 500

        with mock.patch.object(self.engine, "save_state") as save_state:
            result = game_api.do_action(game_api.ActionRequest(
                action="repay_debt", params={"amount": 200}
            ))

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.balance, 300)
        self.assertEqual(self.engine.state.debt_remaining, 5800)
        save_state.assert_called_once()

    def test_api_invalid_amount_is_not_truncated(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 500
        before = (self.engine.state.balance, self.engine.state.debt_remaining)

        result = game_api.do_action(game_api.ActionRequest(
            action="repay_debt", params={"amount": 600}
        ))

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "repayment_exceeds_balance")
        self.assertEqual(
            (self.engine.state.balance, self.engine.state.debt_remaining), before
        )

    def test_query_debt_is_read_only(self):
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False
        self.engine.state.decisions_left = 2
        before = (
            self.engine.state.day,
            self.engine.state.turn,
            self.engine.state.decisions_left,
            self.engine.state.day_end_completed,
            list(self.engine.state.event_history),
        )

        result = game_api.mcp_query_debt()

        self.assertEqual(result, self.engine.get_debt_summary())
        self.assertEqual(
            (
                self.engine.state.day,
                self.engine.state.turn,
                self.engine.state.decisions_left,
                self.engine.state.day_end_completed,
                self.engine.state.event_history,
            ),
            before,
        )

    def test_mcp_state_and_ordinary_action_do_not_add_full_debt_summary(self):
        state = game_api.mcp_state()
        self.assertNotIn("debt_remaining", state)
        self.assertNotIn("initial_debt", state)

        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False
        self.engine.state.balance = 500
        with mock.patch.object(self.engine, "save_state"):
            result = game_api.do_action(game_api.ActionRequest(
                action="repay_debt", params={"amount": 100}
            ))
        self.assertIn("debt_after", result)
        self.assertNotIn("is_overdue", result)


if __name__ == "__main__":
    unittest.main()
