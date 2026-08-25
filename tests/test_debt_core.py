"""启动负债核心规则的定向测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class DebtCoreTests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(
            dir=os.path.join(
                os.environ.get("TEMP")
                or os.environ.get("TMP")
                or tempfile.gettempdir(),
                "camping_plaza_fix_temp",
            )
        )
        self.addCleanup(self._temp_dir.cleanup)
        self.db_path = os.path.join(self._temp_dir.name, "debt_test.db")
        self.engine = CampingPlazaEngine(db_path=self.db_path)

    def _open_repayment_window(self, *, day=25):
        self.engine.state.day = day
        self.engine.state.turn = 6

    def test_new_game_uses_formal_debt_defaults(self):
        state = self.engine.state
        self.assertEqual(state.initial_debt, 6000)
        self.assertEqual(state.debt_remaining, 6000)
        self.assertEqual(state.repayment_deadline_day, 25)

    def test_partial_and_multiple_repayments(self):
        self._open_repayment_window()
        self.engine.state.balance = 5000

        first = self.engine.repay_debt(2000)
        second = self.engine.repay_debt(1000)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(self.engine.state.balance, 2000)
        self.assertEqual(self.engine.state.debt_remaining, 3000)
        self.assertEqual(self.engine.get_debt_summary()["debt_repaid_total"], 3000)

    def test_repayment_does_not_count_as_an_operating_expense(self):
        self._open_repayment_window()
        self.engine.state.balance = 5000
        expenses_before = dict(self.engine.state.today_expenses)
        decisions_before = self.engine.state.decisions_left

        result = self.engine.repay_debt(1000)

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.turn, 6)
        self.assertEqual(self.engine.state.decisions_left, decisions_before)
        self.assertEqual(self.engine.state.today_expenses, expenses_before)

    def test_repayment_is_excluded_from_previous_day_operating_summary(self):
        cases = (
            (500, 0, 300, 500),
            (500, 200, 300, 300),
            (0, 0, 300, 0),
        )
        for income, operating_expense, repayment, expected_net in cases:
            with self.subTest(income=income, operating_expense=operating_expense):
                engine = CampingPlazaEngine(db_path=self.db_path)
                engine.state.day = 25
                engine.state.turn = 6
                engine.state.day_start_balance = 1000
                engine.state.today_income["accommodation"] = income
                engine.state.today_expenses["food"] = operating_expense
                engine.state.balance = 1000 + income - operating_expense
                self.assertTrue(engine.repay_debt(repayment)["success"])
                self.assertEqual(engine.state.today_expenses["food"], operating_expense)
                self.assertEqual(
                    engine.state.balance,
                    1000 + income - operating_expense - repayment,
                )

                with mock.patch.object(engine, "_ensure_today_arrival_plan", return_value=False), \
                     mock.patch.object(engine, "_generate_daily_reservation", return_value=None):
                    engine._new_day()

                summary = engine.state.previous_day_summary
                self.assertEqual(summary["expense_total"], operating_expense)
                self.assertEqual(summary["net_income"], expected_net)
                self.assertEqual(engine.state.debt_remaining, 6000 - repayment)

    def test_exact_payoff_then_repayment_is_rejected(self):
        self._open_repayment_window()
        self.engine.state.balance = 6000

        result = self.engine.repay_debt(6000)
        again = self.engine.repay_debt(1)

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.debt_remaining, 0)
        self.assertFalse(again["success"])
        self.assertEqual(again["error_code"], "debt_already_paid_off")

    def test_invalid_repayments_do_not_change_state_or_record_success_event(self):
        self._open_repayment_window()
        self.engine.state.balance = 5000
        before = (self.engine.state.balance, self.engine.state.debt_remaining)
        invalid_amounts = (0, -1, True, 1.5, "2000", 7000, 21000)

        for amount in invalid_amounts:
            result = self.engine.repay_debt(amount)
            self.assertFalse(result["success"], amount)
            self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), before)

        self.assertFalse(any(
            event.get("event_type") == "repay_debt"
            for event in self.engine.state.event_history
        ))

    def test_repayment_cannot_exceed_remaining_debt(self):
        self._open_repayment_window()
        self.engine.state.balance = 5000
        self.engine.state.debt_remaining = 2000
        before = (self.engine.state.balance, self.engine.state.debt_remaining)

        result = self.engine.repay_debt(2001)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "repayment_exceeds_debt")
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), before)

    def test_repayment_cannot_exceed_balance_after_window_opens(self):
        self._open_repayment_window()
        self.engine.state.balance = 5000
        before = (self.engine.state.balance, self.engine.state.debt_remaining)

        result = self.engine.repay_debt(5001)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "repayment_exceeds_balance")
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), before)

    def test_summary_deadline_semantics_and_success_event(self):
        self._open_repayment_window()
        self.engine.state.balance = 3000
        result = self.engine.repay_debt(1000)
        summary = self.engine.get_debt_summary()

        self.assertTrue(result["success"])
        self.assertEqual(summary["days_until_deadline"], 0)
        self.assertFalse(summary["is_overdue"])
        event = self.engine.state.event_history[-1]
        self.assertEqual(event["event_type"], "repay_debt")
        self.assertEqual(event["action"], "repay_debt")
        self.assertEqual(event["actor"], "player")
        self.assertEqual(event["data"], {
            "amount": 1000,
            "balance_before": 3000,
            "balance_after": 2000,
            "debt_before": 6000,
            "debt_after": 5000,
        })

        self.engine.state.day = 26
        self.assertTrue(self.engine.get_debt_summary()["is_overdue"])
        self.engine.state.debt_remaining = 0
        self.assertFalse(self.engine.get_debt_summary()["is_overdue"])

    def test_save_restore_keeps_debt_fields(self):
        self._open_repayment_window()
        self.engine.state.balance = 7000
        self.assertTrue(self.engine.repay_debt(3000)["success"])
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.initial_debt, 6000)
        self.assertEqual(restored.state.debt_remaining, 3000)
        self.assertEqual(restored.state.repayment_deadline_day, 25)
        self.assertEqual(restored.get_debt_summary()["debt_repaid_total"], 3000)

    def test_repayment_is_rejected_before_day_25_without_state_change(self):
        self.engine.state.day = 24
        self.engine.state.turn = 6
        self.engine.state.balance = 5000
        before = (self.engine.state.balance, self.engine.state.debt_remaining)

        result = self.engine.repay_debt(1000)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "repayment_not_available")
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), before)
        self.assertFalse(any(
            event.get("event_type") == "repay_debt"
            for event in self.engine.state.event_history
        ))

    def test_repayment_is_rejected_outside_turn_six_on_day_25(self):
        self.engine.state.day = 25
        self.engine.state.turn = 5
        self.engine.state.balance = 5000
        before = (self.engine.state.balance, self.engine.state.debt_remaining)

        result = self.engine.repay_debt(1000)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "repayment_not_available")
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), before)

    def test_repayment_remains_available_after_deadline_at_turn_six(self):
        self._open_repayment_window(day=26)
        self.engine.state.balance = 1000

        result = self.engine.repay_debt(1000)

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.debt_remaining, 5000)

    def test_legacy_debt_values_remain_unchanged_until_repayment_opens(self):
        self.engine.state.day = 18
        self.engine.state.turn = 6
        self.engine.state.balance = 4321
        self.engine.state.debt_remaining = 3000
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.balance, 4321)
        self.assertEqual(restored.state.debt_remaining, 3000)
        self.assertFalse(restored.repay_debt(1)["success"])

        restored.state.day = 25
        self.assertTrue(restored.repay_debt(1)["success"])
        self.assertEqual(restored.state.debt_remaining, 2999)

    def test_legacy_paid_off_debt_is_not_restored(self):
        self.engine.state.day = 18
        self.engine.state.balance = 4321
        self.engine.state.debt_remaining = 0
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.balance, 4321)
        self.assertEqual(restored.state.debt_remaining, 0)


if __name__ == "__main__":
    unittest.main()
