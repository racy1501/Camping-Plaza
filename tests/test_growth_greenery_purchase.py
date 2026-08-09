import copy
import os
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class GrowthGreeneryPurchaseTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "growth_greenery_purchase.sqlite")
        self._new_engine()

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def _new_engine(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass
        self.engine = CampingPlazaEngine(db_path=self.db_path)

    def _open_management_phase(self, *, balance=5000):
        self.engine.state.turn = 6
        self.engine.state.balance = balance

    def _snapshot(self):
        return (
            copy.deepcopy(self.engine.state),
            copy.deepcopy(self.engine.tents),
            copy.deepcopy(self.engine.facilities),
        )

    def _assert_snapshot_unchanged(self, before):
        self.assertEqual(self.engine.state, before[0])
        self.assertEqual(self.engine.tents, before[1])
        self.assertEqual(self.engine.facilities, before[2])

    def test_greenery_lv1_purchase_applies_upgrade_effects_only(self):
        self._open_management_phase(balance=1000)
        greenery = self.engine.facilities["greenery"]
        greenery.greenery_satisfaction = 2.0
        decay_before = greenery.greenery_decay_rate
        self.engine.state.successful_greenery_maintenance_count = 4
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        result = self.engine.purchase_growth_project("greenery_lv1")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 600)
        self.assertEqual(result["balance_after"], 400)
        self.assertEqual(greenery.level, 1)
        self.assertEqual(greenery.greenery_satisfaction, 4.0)
        self.assertLessEqual(greenery.greenery_satisfaction, 7.0)
        self.assertTrue(self.engine.state.greenery_processed_today)
        self.assertEqual(self.engine.state.successful_greenery_maintenance_count, 4)
        self.assertEqual(greenery.greenery_decay_rate, decay_before)
        self.assertEqual(result["completed_growth_nodes"], nodes_before + 1)
        self.assertEqual(self.engine.state.decisions_left, 3)

    def test_lv0_greenery_value_four_becomes_six_not_new_cap(self):
        self._open_management_phase()
        greenery = self.engine.facilities["greenery"]
        greenery.greenery_satisfaction = 4.0
        self.engine.state.successful_greenery_maintenance_count = 4

        self.assertTrue(self.engine.purchase_growth_project("greenery_lv1")["success"])

        self.assertEqual(greenery.greenery_satisfaction, 6.0)
        self.assertNotEqual(greenery.greenery_satisfaction, 7.0)

    def test_greenery_lv2_purchase_applies_upgrade_effects_only(self):
        self._open_management_phase(balance=3000)
        greenery = self.engine.facilities["greenery"]
        greenery.level = 1
        greenery.greenery_satisfaction = 5.0
        decay_before = greenery.greenery_decay_rate
        self.engine.state.successful_greenery_maintenance_count = 12

        result = self.engine.purchase_growth_project("greenery_lv2")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 1600)
        self.assertEqual(result["balance_after"], 1400)
        self.assertEqual(greenery.level, 2)
        self.assertEqual(greenery.greenery_satisfaction, 7.0)
        self.assertTrue(self.engine.state.greenery_processed_today)
        self.assertEqual(self.engine.state.successful_greenery_maintenance_count, 12)
        self.assertEqual(greenery.greenery_decay_rate, decay_before)

    def test_upgrade_after_daily_maintenance_still_adds_two_without_counting_maintenance(self):
        self._open_management_phase()
        greenery = self.engine.facilities["greenery"]
        greenery.greenery_satisfaction = 4.0
        self.engine.state.greenery_processed_today = True
        self.engine.state.successful_greenery_maintenance_count = 4

        self.assertTrue(self.engine.purchase_growth_project("greenery_lv1")["success"])

        self.assertEqual(greenery.greenery_satisfaction, 6.0)
        self.assertTrue(self.engine.state.greenery_processed_today)
        self.assertEqual(self.engine.state.successful_greenery_maintenance_count, 4)

    def test_lv2_at_level_zero_fails_atomically(self):
        self._open_management_phase()
        self.engine.state.successful_greenery_maintenance_count = 12
        before = self._snapshot()

        result = self.engine.purchase_growth_project("greenery_lv2")

        self.assertFalse(result["success"])
        self.assertIn("previous_level_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_insufficient_maintenance_count_fails_atomically(self):
        self._open_management_phase()
        self.engine.state.successful_greenery_maintenance_count = 3
        before = self._snapshot()

        result = self.engine.purchase_growth_project("greenery_lv1")

        self.assertFalse(result["success"])
        self.assertIn("greenery_maintenance_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_turn_and_balance_failures_are_atomic(self):
        cases = (
            ("not_turn_6", lambda: setattr(
                self.engine.state, "successful_greenery_maintenance_count", 4
            )),
            (
                "day_end_completed",
                lambda: (
                    self._open_management_phase(),
                    setattr(self.engine.state, "successful_greenery_maintenance_count", 4),
                    setattr(self.engine.state, "day_end_completed", True),
                ),
            ),
            (
                "insufficient_balance",
                lambda: (
                    self._open_management_phase(balance=599),
                    setattr(self.engine.state, "successful_greenery_maintenance_count", 4),
                ),
            ),
        )
        for name, prepare in cases:
            with self.subTest(name=name):
                self._new_engine()
                prepare()
                before = self._snapshot()
                result = self.engine.purchase_growth_project("greenery_lv1")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "growth_project_not_purchasable")
                self._assert_snapshot_unchanged(before)

    def test_repeat_purchase_is_atomic(self):
        self._open_management_phase()
        self.engine.state.successful_greenery_maintenance_count = 4
        self.assertTrue(self.engine.purchase_growth_project("greenery_lv1")["success"])
        before = self._snapshot()

        result = self.engine.purchase_growth_project("greenery_lv1")

        self.assertFalse(result["success"])
        self.assertIn("already_completed", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_same_turn_can_purchase_both_greenery_levels(self):
        self._open_management_phase(balance=5000)
        greenery = self.engine.facilities["greenery"]
        greenery.greenery_satisfaction = 4.0
        self.engine.state.successful_greenery_maintenance_count = 12
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        self.assertTrue(self.engine.purchase_growth_project("greenery_lv1")["success"])
        self.assertTrue(self.engine.purchase_growth_project("greenery_lv2")["success"])

        self.assertEqual(self.engine.state.balance, 2800)
        self.assertEqual(greenery.level, 2)
        self.assertEqual(greenery.greenery_satisfaction, 8.0)
        self.assertEqual(
            self.engine.get_growth_progress()["completed_growth_nodes"], nodes_before + 2
        )
        self.assertEqual(self.engine.state.successful_greenery_maintenance_count, 12)

    def test_purchase_state_survives_snapshot_restore(self):
        self._open_management_phase()
        greenery = self.engine.facilities["greenery"]
        greenery.greenery_satisfaction = 4.0
        self.engine.state.successful_greenery_maintenance_count = 4
        self.assertTrue(self.engine.purchase_growth_project("greenery_lv1")["success"])
        expected_balance = self.engine.state.balance
        expected_level = greenery.level
        expected_satisfaction = greenery.greenery_satisfaction
        expected_processed = self.engine.state.greenery_processed_today
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.balance, expected_balance)
        self.assertEqual(restored.facilities["greenery"].level, expected_level)
        self.assertEqual(
            restored.facilities["greenery"].greenery_satisfaction, expected_satisfaction
        )
        self.assertEqual(restored.state.greenery_processed_today, expected_processed)

    def test_greenery_satisfaction_write_failure_rolls_back(self):
        class FailingGreenery:
            def __init__(self):
                self._level = 0
                self._greenery_satisfaction = 2.0
                self.greenery_decay_rate = 0.5

            @property
            def level(self):
                return self._level

            @level.setter
            def level(self, value):
                self._level = value

            @property
            def greenery_satisfaction(self):
                return self._greenery_satisfaction

            @greenery_satisfaction.setter
            def greenery_satisfaction(self, value):
                if value == 4.0:
                    raise RuntimeError("test greenery write failure")
                self._greenery_satisfaction = value

        self._open_management_phase()
        self.engine.state.successful_greenery_maintenance_count = 4
        self.engine.facilities["greenery"] = FailingGreenery()
        balance_before = self.engine.state.balance
        self.engine.state.greenery_processed_today = False

        result = self.engine.purchase_growth_project("greenery_lv1")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "growth_project_purchase_failed")
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.facilities["greenery"].level, 0)
        self.assertEqual(self.engine.facilities["greenery"].greenery_satisfaction, 2.0)
        self.assertFalse(self.engine.state.greenery_processed_today)


if __name__ == "__main__":
    unittest.main()
