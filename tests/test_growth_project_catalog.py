import copy
import os
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class GrowthProjectCatalogTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "growth_project_catalog.sqlite")
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

    def _catalog(self):
        return {
            project["project_id"]: project
            for project in self.engine.get_growth_project_catalog()
        }

    def _open_management_phase(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 100000

    def test_catalog_has_fixed_twelve_projects_in_order_with_prices(self):
        catalog = self.engine.get_growth_project_catalog()

        self.assertEqual(
            [project["project_id"] for project in catalog],
            [
                "tent_2", "tent_3", "tent_4", "tent_5", "tent_6",
                "dining_lv1", "dining_lv2", "entertainment_lv1",
                "entertainment_lv2", "greenery_lv1", "greenery_lv2",
                "hot_spring",
            ],
        )
        self.assertEqual(
            [project["price"] for project in catalog],
            [600, 1100, 1900, 3200, 4800, 700, 1800, 600, 1600, 600, 1600, 3000],
        )
        self.assertEqual(
            [project["project_id"] for project in CampingPlazaEngine.GROWTH_PROJECT_CATALOG],
            [project["project_id"] for project in catalog],
        )
        self.assertEqual(
            [project["sequence"] for project in CampingPlazaEngine.GROWTH_PROJECT_CATALOG],
            list(range(1, 13)),
        )

    def test_hot_spring_catalog_reports_both_qualification_paths(self):
        self._open_management_phase()
        project = self._catalog()["hot_spring"]
        self.assertFalse(project["prerequisite_met"])
        self.assertFalse(project["operation_requirement_met"])
        self.assertIn("growth_nodes_required", project["unmet_conditions"])
        self.assertIn("served_groups_or_days_required", project["unmet_conditions"])

        for tent_id in range(2, 6):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.state.day = 12
        self.engine.state.campsite_star = 4
        project = self._catalog()["hot_spring"]
        self.assertTrue(project["prerequisite_met"])
        self.assertTrue(project["operation_requirement_met"])
        self.assertTrue(project["can_purchase_now"])
        self.assertEqual(project["progress"]["current_completed_growth_nodes"], 8)
        self.assertEqual(project["progress"]["required_completed_growth_nodes"], 5)

    def test_hot_spring_served_groups_can_replace_day_fallback(self):
        self._open_management_phase()
        self.engine.state.day = 1
        for tent_id in range(2, 6):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.state.total_served_groups = 75
        self.engine.state.campsite_star = 4
        project = self._catalog()["hot_spring"]
        self.assertTrue(project["operation_requirement_met"])

    def test_hot_spring_can_use_day_fallback_with_five_growth_nodes(self):
        self._open_management_phase()
        self.engine.state.day = 12
        self.engine.state.total_served_groups = 0
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[3].is_unlocked = True
        self.engine.tents[4].is_unlocked = True
        self.engine.facilities["dining"].level = 1
        self.engine.facilities["entertainment"].level = 1
        self.engine.state.campsite_star = 4
        project = self._catalog()["hot_spring"]
        self.assertTrue(project["prerequisite_met"])
        self.assertTrue(project["operation_requirement_met"])
        self.assertTrue(project["can_purchase_now"])

    def test_initial_catalog_is_incomplete_and_locked(self):
        catalog = self._catalog()

        self.assertFalse(catalog["tent_2"]["completed"])
        self.assertTrue(catalog["tent_2"]["prerequisite_met"])
        self.assertFalse(catalog["tent_2"]["operation_requirement_met"])
        self.assertIn("operating_day_required", catalog["tent_2"]["unmet_conditions"])
        for project_id in ("tent_3", "tent_4", "tent_5", "tent_6"):
            self.assertIn("previous_tent_required", catalog[project_id]["unmet_conditions"])
        for project_id in (
            "dining_lv1", "dining_lv2", "entertainment_lv1",
            "entertainment_lv2", "greenery_lv1", "greenery_lv2",
        ):
            self.assertFalse(catalog[project_id]["completed"])
            self.assertFalse(catalog[project_id]["can_purchase_now"])
        for project_id in ("dining_lv2", "entertainment_lv2", "greenery_lv2"):
            self.assertFalse(catalog[project_id]["prerequisite_met"])
            self.assertIn("previous_level_required", catalog[project_id]["unmet_conditions"])

    def test_tent_2_requires_day_two_and_open_management_phase(self):
        self._open_management_phase()
        self.engine.state.day = 1
        project = self._catalog()["tent_2"]
        self.assertFalse(project["operation_requirement_met"])
        self.assertTrue(project["management_phase_open"])

        self.engine.state.day = 2
        project = self._catalog()["tent_2"]
        self.assertTrue(project["operation_requirement_met"])
        self.assertTrue(project["can_purchase_now"])

        self.engine.state.day_end_completed = True
        project = self._catalog()["tent_2"]
        self.assertFalse(project["management_phase_open"])
        self.assertFalse(project["can_purchase_now"])

    def test_later_tents_require_previous_tent_but_allow_served_or_day_fallback(self):
        self._open_management_phase()
        self.engine.state.day = 7
        project = self._catalog()["tent_3"]
        self.assertTrue(project["operation_requirement_met"])
        self.assertFalse(project["prerequisite_met"])
        self.assertIn("previous_tent_required", project["unmet_conditions"])

        self.engine.tents[2].is_unlocked = True
        self.engine.state.day = 2
        self.engine.state.total_served_groups = 15
        project = self._catalog()["tent_3"]
        self.assertTrue(project["can_purchase_now"])
        self.assertEqual(project["progress"]["required_served_groups"], 15)
        self.assertEqual(project["progress"]["fallback_operating_day"], 7)

        requirements = {
            "tent_4": (3, 50, 12),
            "tent_5": (4, 90, 17),
            "tent_6": (5, 150, 23),
        }
        for project_id, (previous_tent, served, fallback_day) in requirements.items():
            self.engine.tents[previous_tent].is_unlocked = False
            self.engine.state.total_served_groups = served
            self.engine.state.day = 2
            self.assertFalse(self._catalog()[project_id]["prerequisite_met"])
            self.engine.tents[previous_tent].is_unlocked = True
            self.assertTrue(self._catalog()[project_id]["operation_requirement_met"])
            self.engine.state.total_served_groups = 0
            self.engine.state.day = fallback_day
            self.assertTrue(self._catalog()[project_id]["operation_requirement_met"])

    def test_dining_levels_require_correct_level_and_successful_dining_counts(self):
        self._open_management_phase()
        self.engine.state.successful_dining_groups = 8
        project = self._catalog()["dining_lv1"]
        self.assertTrue(project["can_purchase_now"])
        self.assertFalse(self._catalog()["dining_lv2"]["prerequisite_met"])

        self.engine.facilities["dining"].level = 1
        project = self._catalog()["dining_lv1"]
        self.assertTrue(project["completed"])
        self.assertFalse(project["can_purchase_now"])
        self.assertIn("already_completed", project["unmet_conditions"])

        self.engine.state.successful_dining_groups = 35
        project = self._catalog()["dining_lv2"]
        self.assertFalse(project["operation_requirement_met"])
        self.engine.state.successful_dining_groups = 36
        self.assertTrue(self._catalog()["dining_lv2"]["can_purchase_now"])

    def test_entertainment_and_greenery_levels_use_their_own_counters(self):
        self._open_management_phase()
        self.engine.state.successful_paid_entertainment_groups = 8
        self.assertTrue(self._catalog()["entertainment_lv1"]["can_purchase_now"])
        self.engine.facilities["entertainment"].level = 1
        self.engine.state.successful_paid_entertainment_groups = 31
        self.assertFalse(self._catalog()["entertainment_lv2"]["operation_requirement_met"])
        self.engine.state.successful_paid_entertainment_groups = 32
        self.assertTrue(self._catalog()["entertainment_lv2"]["can_purchase_now"])

        self.engine.state.successful_greenery_maintenance_count = 4
        self.assertTrue(self._catalog()["greenery_lv1"]["can_purchase_now"])
        self.engine.facilities["greenery"].level = 1
        self.engine.state.successful_greenery_maintenance_count = 11
        self.assertFalse(self._catalog()["greenery_lv2"]["operation_requirement_met"])
        self.engine.state.successful_greenery_maintenance_count = 12
        self.assertTrue(self._catalog()["greenery_lv2"]["can_purchase_now"])

    def test_insufficient_balance_and_non_turn_six_prevent_purchase(self):
        self._open_management_phase()
        self.engine.state.day = 2
        self.engine.state.balance = 599
        project = self._catalog()["tent_2"]
        self.assertFalse(project["can_purchase_now"])
        self.assertIn("insufficient_balance", project["unmet_conditions"])

        self.engine.state.balance = 600
        self.engine.state.turn = 5
        project = self._catalog()["tent_2"]
        self.assertFalse(project["can_purchase_now"])
        self.assertIn("turn_6_required", project["unmet_conditions"])

    def test_catalog_query_does_not_change_state(self):
        self._open_management_phase()
        self.engine.state.day = 7
        self.engine.state.total_served_groups = 15
        self.engine.tents[2].is_unlocked = True
        before_state = copy.deepcopy(self.engine.state)
        before_tents = copy.deepcopy(self.engine.tents)
        before_facilities = copy.deepcopy(self.engine.facilities)

        self.engine.get_growth_project_catalog()

        self.assertEqual(self.engine.state, before_state)
        self.assertEqual(self.engine.tents, before_tents)
        self.assertEqual(self.engine.facilities, before_facilities)

    def test_invalid_facility_level_raises_without_correction(self):
        self.engine.facilities["greenery"].level = 3

        with self.assertRaisesRegex(ValueError, "greenery=3"):
            self.engine.get_growth_project_catalog()

        self.assertEqual(self.engine.facilities["greenery"].level, 3)


if __name__ == "__main__":
    unittest.main()
