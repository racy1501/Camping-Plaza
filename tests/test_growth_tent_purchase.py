import copy
import os
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class GrowthTentPurchaseTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "growth_tent_purchase.sqlite")
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass
        self.engine = CampingPlazaEngine(db_path=self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def _open_management_phase(self, *, balance=20000, day=2):
        self.engine.state.turn = 6
        self.engine.state.day = day
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

    def test_unknown_project_fails_without_changing_state(self):
        before = self._snapshot()

        result = self.engine.purchase_growth_project("not_a_project")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "unknown_growth_project")
        self._assert_snapshot_unchanged(before)

    def test_unimplemented_project_is_rejected_without_changing_state(self):
        before = self._snapshot()
        original_catalog = CampingPlazaEngine.GROWTH_PROJECT_CATALOG
        future_project = {
            "project_id": "future_project",
            "category": "future",
            "display_name": "未来项目",
            "price": 1,
            "sequence": 99,
        }

        try:
            CampingPlazaEngine.GROWTH_PROJECT_CATALOG = original_catalog + (
                future_project,
            )
            result = self.engine.purchase_growth_project("future_project")
        finally:
            CampingPlazaEngine.GROWTH_PROJECT_CATALOG = original_catalog

        self.assertFalse(result["success"])
        self.assertEqual(result["category"], "future")
        self.assertEqual(result["error_code"], "growth_project_category_not_implemented")
        self._assert_snapshot_unchanged(before)

    def test_non_purchasable_cases_are_atomic(self):
        cases = []

        cases.append(("non_turn_6", lambda: None))

        def day_end_completed():
            self._open_management_phase()
            self.engine.state.day_end_completed = True

        cases.append(("day_end_completed", day_end_completed))

        def insufficient_balance():
            self._open_management_phase(balance=599)

        cases.append(("insufficient_balance", insufficient_balance))

        def unmet_operation_requirement():
            self._open_management_phase(day=1)

        cases.append(("unmet_operation_requirement", unmet_operation_requirement))

        for name, prepare in cases:
            with self.subTest(name=name):
                self.setUp()
                prepare()
                before = self._snapshot()
                result = self.engine.purchase_growth_project("tent_2")
                self.assertFalse(result["success"])
                self.assertEqual(result["error_code"], "growth_project_not_purchasable")
                self._assert_snapshot_unchanged(before)
                self.tearDown()

    def test_missing_previous_tent_fails_without_changing_state(self):
        self._open_management_phase(day=7)
        before = self._snapshot()

        result = self.engine.purchase_growth_project("tent_3")

        self.assertFalse(result["success"])
        self.assertIn("previous_tent_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_tent_2_purchase_updates_only_required_state(self):
        self._open_management_phase(balance=1000)
        tent = self.engine.tents[2]
        tent.status = "cleaning"
        tent.occupied_by = 999
        absolute_turn = self.engine._absolute_turn()
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        result = self.engine.purchase_growth_project("tent_2")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 600)
        self.assertEqual(result["balance_before"], 1000)
        self.assertEqual(result["balance_after"], 400)
        self.assertTrue(tent.is_unlocked)
        self.assertEqual(tent.status, "cleaning")
        self.assertEqual(tent.occupied_by, 999)
        self.assertGreater(tent.next_breakdown_turn, absolute_turn)
        self.assertEqual(result["completed_growth_nodes"], nodes_before + 1)
        self.assertEqual(self.engine.state.decisions_left, 3)

    def test_repeat_purchase_is_atomic_and_does_not_reset_breakdown(self):
        self._open_management_phase()
        self.assertTrue(self.engine.purchase_growth_project("tent_2")["success"])
        before = self._snapshot()

        result = self.engine.purchase_growth_project("tent_2")

        self.assertFalse(result["success"])
        self.assertIn("already_completed", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

    def test_tent_3_can_use_served_groups_or_day_fallback(self):
        self._open_management_phase(day=2)
        self.engine.tents[2].is_unlocked = True
        self.engine.state.total_served_groups = 15
        self.assertTrue(self.engine.purchase_growth_project("tent_3")["success"])

        self.setUp()
        self._open_management_phase(day=7)
        self.engine.tents[2].is_unlocked = True
        self.assertTrue(self.engine.purchase_growth_project("tent_3")["success"])

    def test_same_turn_purchase_immediately_unlocks_the_next_prerequisite(self):
        self._open_management_phase()
        self.engine.state.total_served_groups = 15

        self.assertTrue(self.engine.purchase_growth_project("tent_2")["success"])
        result = self.engine.purchase_growth_project("tent_3")

        self.assertTrue(result["success"])
        self.assertTrue(self.engine.tents[3].is_unlocked)

    def test_same_turn_can_purchase_all_tents_when_conditions_and_balance_allow(self):
        self._open_management_phase(balance=20000)
        self.engine.state.total_served_groups = 150
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        for project_id in ("tent_2", "tent_3", "tent_4", "tent_5", "tent_6"):
            self.assertTrue(self.engine.purchase_growth_project(project_id)["success"])

        self.assertEqual(self.engine.state.balance, 8400)
        self.assertTrue(all(self.engine.tents[tent_id].is_unlocked for tent_id in range(2, 7)))
        self.assertEqual(
            self.engine.get_growth_progress()["completed_growth_nodes"], nodes_before + 5
        )

    def test_purchase_state_survives_snapshot_restore(self):
        self._open_management_phase()
        self.assertTrue(self.engine.purchase_growth_project("tent_2")["success"])
        expected_balance = self.engine.state.balance
        expected_tent = copy.deepcopy(self.engine.tents[2])
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.balance, expected_balance)
        self.assertEqual(restored.tents[2], expected_tent)

    def _qualify_hot_spring(self, *, day=1, balance=10000):
        self._open_management_phase(balance=balance, day=day)
        for tent_id in range(2, 6):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.state.total_served_groups = 150 if day < 25 else 0

    def test_hot_spring_purchase_is_atomic_repeat_safe_and_persistent(self):
        self._qualify_hot_spring()
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]
        result = self.engine.purchase_growth_project("hot_spring")
        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 3000)
        self.assertEqual(result["balance_before"], 10000)
        self.assertEqual(result["balance_after"], 7000)
        self.assertTrue(self.engine.state.hot_spring_built)
        self.assertEqual(result["completed_growth_nodes"], nodes_before)
        self.assertEqual(self.engine.get_growth_progress()["completed_growth_nodes"], nodes_before)
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(restored.state.hot_spring_built)
        self.assertEqual(restored.state.turn, 6)
        before_balance = restored.state.balance
        repeated = restored.purchase_growth_project("hot_spring")
        self.assertFalse(repeated["success"])
        self.assertIn("already_completed", repeated["unmet_conditions"])
        self.assertEqual(restored.state.balance, before_balance)

    def test_hot_spring_qualification_failures_are_atomic(self):
        cases = (
            (1, 10000, "served_groups_or_days_required"),
            (25, 2999, "insufficient_balance"),
        )
        for day, balance, expected_code in cases:
            with self.subTest(day=day, balance=balance):
                self.setUp()
                self._qualify_hot_spring(day=day, balance=balance)
                if day == 1:
                    self.engine.state.total_served_groups = 0
                before = self._snapshot()
                result = self.engine.purchase_growth_project("hot_spring")
                self.assertFalse(result["success"])
                self.assertIn(expected_code, result["unmet_conditions"])
                self._assert_snapshot_unchanged(before)
                self.tearDown()

    def test_hot_spring_requires_five_nodes_and_open_unsettled_turn_six(self):
        self._open_management_phase(balance=10000, day=25)
        self.engine.state.total_served_groups = 150
        before = self._snapshot()
        result = self.engine.purchase_growth_project("hot_spring")
        self.assertFalse(result["success"])
        self.assertIn("growth_nodes_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

        self._qualify_hot_spring()
        self.engine.state.turn = 5
        before = self._snapshot()
        result = self.engine.purchase_growth_project("hot_spring")
        self.assertFalse(result["success"])
        self.assertIn("turn_6_required", result["unmet_conditions"])
        self._assert_snapshot_unchanged(before)

        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True
        before = self._snapshot()
        result = self.engine.purchase_growth_project("hot_spring")
        self.assertFalse(result["success"])
        self._assert_snapshot_unchanged(before)


    def test_breakdown_setup_failure_restores_partial_changes(self):
        self._open_management_phase()
        before = self._snapshot()
        original_set_next_breakdown = self.engine._set_next_breakdown

        def fail_set_next_breakdown(tent):
            raise RuntimeError("test breakdown failure")

        self.engine._set_next_breakdown = fail_set_next_breakdown
        result = self.engine.purchase_growth_project("tent_2")
        self.engine._set_next_breakdown = original_set_next_breakdown

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "growth_project_purchase_failed")
        self._assert_snapshot_unchanged(before)


if __name__ == "__main__":
    unittest.main()
