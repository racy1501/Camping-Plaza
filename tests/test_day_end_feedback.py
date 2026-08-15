import unittest
from pathlib import Path


class DayEndFeedbackFrontendTests(unittest.TestCase):
    def test_day_end_feedback_distinguishes_action_outcomes(self):
        source = Path(
            "camping_plaza/frontend/scripts/overview.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function formatDayEndResultSummary(result)", source)
        self.assertIn("action_execution_status", source)
        self.assertIn("case 'partial_success':", source)
        self.assertIn("case 'all_failed':", source)
        self.assertIn("未执行：${failures}", source)
        self.assertIn("失败：${reason}", source)

    def test_day_end_does_not_claim_next_day_before_start_succeeds(self):
        source = Path(
            "camping_plaza/frontend/scripts/overview.js"
        ).read_text(encoding="utf-8")

        self.assertNotIn("日终清单已提交，已开启新的一天", source)
        self.assertIn("请确认进入新的一天", source)
        self.assertIn("setActionMessage('已进入新的一天。');", source)

    def test_day_end_budget_hint_uses_neutral_parallel_wording(self):
        source = Path(
            "camping_plaza/frontend/scripts/overview.js"
        ).read_text(encoding="utf-8")

        self.assertIn("day_end_budget_hint", source)
        self.assertNotIn("其他可选日终行动", source)
        self.assertNotIn("先还款", source)
        self.assertNotIn("优先还款", source)

    def test_turn_plan_feedback_surfaces_action_failures(self):
        source = Path(
            "camping_plaza/frontend/scripts/overview.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function formatTurnPlanResultSummary(result)", source)
        self.assertIn("result.action_failures", source)
        self.assertIn("部分动作未完成", source)


if __name__ == "__main__":
    unittest.main()
