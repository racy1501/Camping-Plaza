import copy
import os
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class GrowthDiningPurchaseTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "growth_dining_purchase.sqlite")
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

    def _dining_hidden_fields(self):
        dining = self.engine.facilities["dining"]
        return (
            dining.dining_spend_probability,
            dining.dining_income_multiplier,
            dining.dining_satisfaction,
        )

    def test_dining_lv1_purchase_changes_only_level_and_balance(self):
        self._open_management_phase(balance=1000)
        self.engine.state.campsite_star = 2
        hidden_before = self._dining_hidden_fields()
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        result = self.engine.purchase_growth_project("dining_lv1")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 700)
        self.assertEqual(result["balance_before"], 1000)
        self.assertEqual(result["balance_after"], 300)
        self.assertEqual(result["previous_level"], 0)
        self.assertEqual(result["target_level"], 1)
        self.assertEqual(self.engine.facilities["dining"].level, 1)
        self.assertEqual(self._dining_hidden_fields(), hidden_before)
        self.assertEqual(result["completed_growth_nodes"], nodes_before + 1)
        self.assertEqual(self.engine.state.decisions_left, 5)

    def test_dining_lv2_purchase_changes_only_level_and_balance(self):
        self._open_management_phase(balance=3000)
        self.engine.facilities["dining"].level = 1
        self.engine.state.campsite_star = 3
        hidden_before = self._dining_hidden_fields()

        result = self.engine.purchase_growth_project("dining_lv2")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 1800)
        self.assertEqual(result["balance_after"], 1200)
        self.assertEqual(result["previous_level"], 1)
        self.assertEqual(result["target_level"], 2)
        self.assertEqual(self.engine.facilities["dining"].level, 2)
        self.assertEqual(self._dining_hidden_fields(), hidden_before)

    def test_lv2_at_level_zero_fails_atomically(self):
        self._open_management_phase()
        self.engine.state.campsite_star = 3
        before = self._snapshot()

        result = self.engine.purchase_growth_project("dining_lv2")

        self.assertFalse(result["success"])
        self.assertIn("previous_level_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_insufficient_campsite_star_fails_atomically(self):
        self._open_management_phase()
        before = self._snapshot()

        result = self.engine.purchase_growth_project("dining_lv1")

        self.assertFalse(result["success"])
        self.assertIn("campsite_star_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_turn_and_balance_failures_are_atomic(self):
        cases = (
            ("not_turn_6", lambda: setattr(self.engine.state, "campsite_star", 2)),
            (
                "day_end_completed",
                lambda: (
                    self._open_management_phase(),
                    setattr(self.engine.state, "campsite_star", 2),
                    setattr(self.engine.state, "day_end_completed", True),
                ),
            ),
            (
                "insufficient_balance",
                lambda: (
                    self._open_management_phase(balance=699),
                    setattr(self.engine.state, "campsite_star", 2),
                ),
            ),
        )
        for name, prepare in cases:
            with self.subTest(name=name):
                self._new_engine()
                prepare()
                before = self._snapshot()
                result = self.engine.purchase_growth_project("dining_lv1")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "growth_project_not_purchasable")
                self._assert_snapshot_unchanged(before)

    def test_repeat_purchase_is_atomic(self):
        self._open_management_phase()
        self.engine.state.campsite_star = 2
        self.assertTrue(self.engine.purchase_growth_project("dining_lv1")["success"])
        before = self._snapshot()

        result = self.engine.purchase_growth_project("dining_lv1")

        self.assertFalse(result["success"])
        self.assertIn("already_completed", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_same_turn_can_purchase_both_dining_levels(self):
        self._open_management_phase(balance=5000)
        self.engine.state.campsite_star = 3
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        self.assertTrue(self.engine.purchase_growth_project("dining_lv1")["success"])
        self.assertTrue(self.engine.purchase_growth_project("dining_lv2")["success"])

        self.assertEqual(self.engine.state.balance, 2500)
        self.assertEqual(self.engine.facilities["dining"].level, 2)
        self.assertEqual(
            self.engine.get_growth_progress()["completed_growth_nodes"], nodes_before + 2
        )

    def test_purchase_state_survives_snapshot_restore(self):
        self._open_management_phase()
        self.engine.state.campsite_star = 2
        self.assertTrue(self.engine.purchase_growth_project("dining_lv1")["success"])
        expected_balance = self.engine.state.balance
        expected_level = self.engine.facilities["dining"].level
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.balance, expected_balance)
        self.assertEqual(restored.facilities["dining"].level, expected_level)

    def test_dining_level_write_failure_rolls_back(self):
        class FailingDining:
            def __init__(self):
                self._level = 0

            @property
            def level(self):
                return self._level

            @level.setter
            def level(self, value):
                if value == 1:
                    raise RuntimeError("test dining write failure")
                self._level = value

        self._open_management_phase()
        self.engine.state.campsite_star = 2
        self.engine.facilities["dining"] = FailingDining()
        balance_before = self.engine.state.balance

        result = self.engine.purchase_growth_project("dining_lv1")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "growth_project_purchase_failed")
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.facilities["dining"].level, 0)


if __name__ == "__main__":
    unittest.main()
