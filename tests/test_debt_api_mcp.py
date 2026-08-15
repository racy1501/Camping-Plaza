"""Turn 6 日终候选与债务主链测试。"""

import sys
import unittest

sys.path.insert(0, "camping_plaza")

import game_api
from game_engine import CampingPlazaEngine


class DebtApiMcpTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None
        self.original_engine = game_api.engine
        game_api.engine = self.engine
        self.engine.state.turn = 6
        self.engine.state.balance = 10000
        self.engine.tents[1].status = "broken"
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[2].status = "cleaning"
        self.engine.facilities["greenery"].greenery_satisfaction = 1.0
        self.engine.state.successful_dining_groups = 8

    def tearDown(self):
        game_api.engine = self.original_engine

    def _human_candidates(self):
        return game_api.get_human_actions()["day_end_action_candidates"]

    def _mcp_candidates(self):
        entry = game_api.mcp_available_actions()["available_actions"][0]
        if entry["action"] == "start_next_day":
            return []
        self.assertEqual(entry["action"], "submit_day_end_actions")
        return entry["day_end_action_candidates"]

    @staticmethod
    def _facts(candidates):
        return [
            {
                key: candidate.get(key)
                for key in (
                    "action", "params", "required_params", "cost", "enabled", "reason",
                    "min_amount", "max_amount", "portions",
                )
                if key in candidate
            }
            for candidate in candidates
        ]

    def test_turn6_human_and_mcp_share_all_day_end_candidate_facts(self):
        human = self._human_candidates()
        mcp = self._mcp_candidates()

        self.assertEqual(self._facts(human), self._facts(mcp))
        actions = [candidate["action"] for candidate in mcp]
        self.assertIn("repay_debt", actions)
        self.assertIn("clean_tents", actions)
        self.assertIn("repair_tent", actions)
        self.assertIn("buy_food_package", actions)
        self.assertIn("manage_greenery", actions)
        self.assertIn("purchase_growth_project", actions)

    def test_repayment_candidate_is_a_day_end_action_with_shared_conditions(self):
        human_repay = next(
            candidate for candidate in self._human_candidates()
            if candidate["action"] == "repay_debt"
        )
        mcp_repay = next(
            candidate for candidate in self._mcp_candidates()
            if candidate["action"] == "repay_debt"
        )

        self.assertEqual(human_repay["params"], {"amount": None})
        self.assertEqual(human_repay["required_params"][0]["name"], "amount")
        self.assertEqual(human_repay["max_amount"], self.engine.state.debt_remaining)
        self.assertTrue(human_repay["enabled"])
        self.assertEqual(
            self._facts([human_repay]), self._facts([mcp_repay])
        )

        self.engine.state.balance = 0
        disabled_human = next(
            candidate for candidate in self._human_candidates()
            if candidate["action"] == "repay_debt"
        )
        disabled_mcp = next(
            candidate for candidate in self._mcp_candidates()
            if candidate["action"] == "repay_debt"
        )
        self.assertFalse(disabled_human["enabled"])
        self.assertEqual(disabled_human["reason"], "金币不足")
        self.assertEqual(self._facts([disabled_human]), self._facts([disabled_mcp]))

    def test_turn6_summaries_proactively_expose_debt_facts(self):
        human_summary = game_api.get_human_actions()["decision_summary"]
        mcp_summary = game_api.mcp_available_actions()["decision_summary"]
        for summary in (human_summary, mcp_summary):
            self.assertEqual(summary["debt_remaining"], self.engine.state.debt_remaining)
            self.assertEqual(
                summary["repayment_deadline_day"],
                self.engine.state.repayment_deadline_day,
            )
            self.assertEqual(summary["balance"], self.engine.state.balance)
            self.assertIn("today_net_income", summary)

    def test_repay_debt_submits_with_other_day_end_actions(self):
        result = game_api.submit_day_end(game_api.DayEndRequest(day_end_actions=[
            game_api.ActionRequest(
                action="repay_debt", params={"amount": 200}
            ),
            game_api.ActionRequest(
                action="clean_tents", params={"tent_ids": [2]}
            ),
        ]))

        self.assertTrue(result["success"])
        self.assertTrue(result["day_end_completed"])
        results = {item["action"]: item for item in result["results"]}
        self.assertTrue(results["repay_debt"]["success"])
        self.assertTrue(results["clean_tents"]["success"])
        self.assertEqual(self.engine.state.debt_remaining, 5800)

    def test_completed_day_end_hides_candidates_in_both_catalogs(self):
        self.engine.state.day_end_completed = True
        self.assertEqual(self._human_candidates(), [])
        self.assertEqual(self._mcp_candidates(), [])

    def test_query_debt_is_read_only(self):
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


if __name__ == "__main__":
    unittest.main()
