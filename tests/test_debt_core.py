"""启动负债核心规则的定向测试。"""

import os
import json
import sqlite3
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

    def _open_repayment_window(self, *, day=26):
        self.engine.state.day = day
        self.engine.state.turn = 6
        self.engine.state.startup_debt_settlement_completed = True

    def test_new_game_uses_formal_debt_defaults(self):
        state = self.engine.state
        self.assertEqual(state.balance, 1000)
        self.assertEqual(state.initial_debt, 21000)
        self.assertEqual(state.debt_remaining, 21000)
        self.assertEqual(state.repayment_deadline_day, 26)
        self.assertFalse(state.startup_debt_settlement_completed)

    def test_partial_and_multiple_repayments(self):
        self._open_repayment_window()
        self.engine.state.balance = 5000

        first = self.engine.repay_debt(2000)
        second = self.engine.repay_debt(1000)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(self.engine.state.balance, 2000)
        self.assertEqual(self.engine.state.debt_remaining, 18000)
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
                engine.state.day = 26
                engine.state.turn = 6
                engine.state.startup_debt_settlement_completed = True
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
                self.assertEqual(engine.state.debt_remaining, 21000 - repayment)

    def test_exact_payoff_then_repayment_is_rejected(self):
        self._open_repayment_window()
        self.engine.state.debt_remaining = 6000
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
            "debt_before": 21000,
            "debt_after": 20000,
        })

        self.engine.state.day = 26
        self.assertFalse(self.engine.get_debt_summary()["is_overdue"])
        self.engine.state.debt_remaining = 0
        self.assertFalse(self.engine.get_debt_summary()["is_overdue"])

    def test_save_restore_keeps_debt_fields(self):
        self._open_repayment_window()
        self.engine.state.balance = 7000
        self.assertTrue(self.engine.repay_debt(3000)["success"])
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.initial_debt, 21000)
        self.assertEqual(restored.state.debt_remaining, 18000)
        self.assertEqual(restored.state.repayment_deadline_day, 26)
        self.assertTrue(restored.state.startup_debt_settlement_completed)
        self.assertEqual(restored.get_debt_summary()["debt_repaid_total"], 3000)

    def test_repayment_is_rejected_through_day_25_without_state_change(self):
        for day in (1, 25):
            with self.subTest(day=day):
                self.engine.state.day = day
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

    def test_repayment_is_rejected_outside_turn_six_after_settlement(self):
        self.engine.state.day = 26
        self.engine.state.turn = 5
        self.engine.state.startup_debt_settlement_completed = True
        self.engine.state.balance = 5000
        before = (self.engine.state.balance, self.engine.state.debt_remaining)

        result = self.engine.repay_debt(1000)

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "repayment_not_available")
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), before)

    def test_repayment_is_available_after_settlement_at_turn_six(self):
        self._open_repayment_window(day=26)
        self.engine.state.balance = 1000

        result = self.engine.repay_debt(1000)

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.debt_remaining, 20000)

    def test_day_20_and_day_25_morning_reminders_are_emitted_once(self):
        for day, expected in (
            (19, "Day 26 晨间将统一结算启动资金"),
            (24, "明早将结算启动资金"),
        ):
            with self.subTest(day=day):
                self.engine.state.day = day
                result = {"events": []}
                with mock.patch.object(self.engine, "_ensure_today_arrival_plan", return_value=False), \
                     mock.patch.object(self.engine, "_generate_daily_reservation", return_value=None):
                    self.engine._new_day(result)
                self.assertEqual(self.engine.state.day, day + 1)
                self.assertIn(expected, "".join(result["events"]))

    def test_day_26_auto_settlement_is_correct_and_idempotent(self):
        self.engine.state.day = 25
        self.engine.state.balance = 22000
        result = {"events": []}
        with mock.patch.object(self.engine, "_ensure_today_arrival_plan", return_value=False), \
             mock.patch.object(self.engine, "_generate_daily_reservation", return_value=None):
            self.engine._new_day(result)
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), (1000, 0))
        self.assertTrue(self.engine.state.startup_debt_settlement_completed)
        self.assertEqual(
            set(self.engine.state.unlocked_achievement_ids) & self.engine.DEBT_RESULT_ACHIEVEMENT_IDS,
            {"debt_paid_by_deadline"},
        )
        self.engine._settle_startup_debt_on_day_26(result)
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), (1000, 0))

    def test_day_26_insufficient_balance_keeps_save_playable_and_result_fixed(self):
        self.engine.state.day = 25
        self.engine.state.balance = 2000
        result = {"events": []}
        with mock.patch.object(self.engine, "_ensure_today_arrival_plan", return_value=False), \
             mock.patch.object(self.engine, "_generate_daily_reservation", return_value=None):
            self.engine._new_day(result)
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), (0, 19000))
        self.assertIn("仍可继续经营", "".join(result["events"]))
        self.assertEqual(
            set(self.engine.state.unlocked_achievement_ids) & self.engine.DEBT_RESULT_ACHIEVEMENT_IDS,
            {"debt_unpaid_by_deadline"},
        )
        self.engine.state.turn = 6
        self.engine.state.balance = 1000
        self.assertTrue(self.engine.repay_debt(1000)["success"])
        self.assertEqual(self.engine.state.debt_remaining, 18000)
        self.assertEqual(
            set(self.engine.state.unlocked_achievement_ids) & self.engine.DEBT_RESULT_ACHIEVEMENT_IDS,
            {"debt_unpaid_by_deadline"},
        )

    def test_save_reload_does_not_repeat_day_26_settlement(self):
        self.engine.state.day = 25
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True
        self.engine.state.balance = 2000
        with mock.patch.object(self.engine, "_ensure_today_arrival_plan", return_value=False), \
             mock.patch.object(self.engine, "_generate_daily_reservation", return_value=None):
            self.engine.start_next_day()
        self.assertEqual((self.engine.state.balance, self.engine.state.debt_remaining), (0, 19000))
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        before = (restored.state.balance, restored.state.debt_remaining)
        restored._settle_startup_debt_on_day_26({"events": []})
        self.assertTrue(restored.state.startup_debt_settlement_completed)
        self.assertEqual((restored.state.balance, restored.state.debt_remaining), before)

    def test_legacy_debt_values_remain_unchanged_until_day_26_settlement(self):
        self.engine.state.day = 18
        self.engine.state.turn = 6
        self.engine.state.balance = 4321
        self.engine.state.initial_debt = 6000
        self.engine.state.debt_remaining = 3000
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.balance, 4321)
        self.assertEqual(restored.state.initial_debt, 6000)
        self.assertEqual(restored.state.debt_remaining, 3000)
        self.assertFalse(restored.repay_debt(1)["success"])

        restored.state.day = 26
        restored.state.startup_debt_settlement_completed = True
        self.assertTrue(restored.repay_debt(1)["success"])
        self.assertEqual(restored.state.debt_remaining, 2999)

    def test_legacy_paid_off_debt_is_not_restored(self):
        self.engine.state.day = 18
        self.engine.state.balance = 4321
        self.engine.state.initial_debt = 6000
        self.engine.state.debt_remaining = 0
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.balance, 4321)
        self.assertEqual(restored.state.initial_debt, 6000)
        self.assertEqual(restored.state.debt_remaining, 0)

    def test_legacy_full_debt_is_not_upgraded(self):
        self.engine.state.day = 18
        self.engine.state.balance = 4321
        self.engine.state.initial_debt = 6000
        self.engine.state.debt_remaining = 6000
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.balance, 4321)
        self.assertEqual(restored.state.initial_debt, 6000)
        self.assertEqual(restored.state.debt_remaining, 6000)

    def test_legacy_snapshot_before_day_26_enters_new_settlement(self):
        self.engine.state.day = 18
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True
        self.engine.state.balance = 2000
        self.engine.state.initial_debt = 6000
        self.engine.state.debt_remaining = 3000
        self.assertTrue(self.engine.save_state())
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute("SELECT snapshot_json FROM runtime_snapshot").fetchone()[0]
            snapshot = json.loads(raw)
            snapshot["state"].pop("startup_debt_settlement_completed", None)
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ?",
                (json.dumps(snapshot, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertFalse(restored.state.startup_debt_settlement_completed)
        restored.state.day = 25
        restored.state.turn = 6
        restored.state.day_end_completed = True
        with mock.patch.object(restored, "_ensure_today_arrival_plan", return_value=False), \
             mock.patch.object(restored, "_generate_daily_reservation", return_value=None):
            result = restored.start_next_day()
        self.assertEqual(restored.state.day, 26)
        self.assertEqual((restored.state.balance, restored.state.debt_remaining), (0, 1000))
        self.assertEqual(
            set(restored.state.unlocked_achievement_ids) & restored.DEBT_RESULT_ACHIEVEMENT_IDS,
            {"debt_unpaid_by_deadline"},
        )
        del restored

    def test_legacy_snapshot_already_on_day_26_is_not_retroactively_charged(self):
        self.engine.state.day = 26
        self.engine.state.turn = 1
        self.engine.state.balance = 4321
        self.engine.state.initial_debt = 6000
        self.engine.state.debt_remaining = 3000
        self.assertTrue(self.engine.save_state())
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute("SELECT snapshot_json FROM runtime_snapshot").fetchone()[0]
            snapshot = json.loads(raw)
            snapshot["state"].pop("startup_debt_settlement_completed", None)
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ?",
                (json.dumps(snapshot, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(restored.state.startup_debt_settlement_completed)
        self.assertEqual((restored.state.balance, restored.state.debt_remaining), (4321, 3000))
        del restored


if __name__ == "__main__":
    unittest.main()
