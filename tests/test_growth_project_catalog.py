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

    def test_catalog_has_fixed_projects_in_order_with_prices(self):
        catalog = self.engine.get_growth_project_catalog()

        self.assertEqual(
            [project["project_id"] for project in catalog],
            [
                "tent_2", "tent_3", "tent_4", "tent_5", "tent_6",
                "dining_lv1", "dining_lv2", "entertainment_lv1",
                "entertainment_lv2", "greenery_lv1", "greenery_lv2",
                "hot_spring", "nature_observation_station",
            ],
        )
        self.assertEqual(
            [project["price"] for project in catalog],
            [600, 1100, 1900, 3200, 4800, 700, 1800, 600, 1600, 600, 1600, 3000, 800],
        )
        self.assertEqual(
            [project["project_id"] for project in CampingPlazaEngine.GROWTH_PROJECT_CATALOG],
            [project["project_id"] for project in catalog],
        )
        self.assertEqual(
            [project["sequence"] for project in CampingPlazaEngine.GROWTH_PROJECT_CATALOG],
            list(range(1, 14)),
        )

    def test_nature_observation_station_requires_three_star_and_is_not_growth_node(self):
        self._open_management_phase()
        nodes_before = self.engine.get_growth_progress()["completed_growth_nodes"]

        project = self._catalog()["nature_observation_station"]
        self.assertFalse(project["operation_requirement_met"])
        self.assertIn("campsite_star_required", project["unmet_conditions"])

        self.engine.state.campsite_star = 3
        project = self._catalog()["nature_observation_station"]
        self.assertTrue(project["can_purchase_now"])
        result = self.engine.purchase_growth_project("nature_observation_station")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 800)
        self.assertTrue(self.engine.state.nature_observation_station_built)
        self.assertEqual(
            self.engine.get_growth_progress()["completed_growth_nodes"], nodes_before
        )

        balance_after_purchase = self.engine.state.balance
        repeated = self.engine.purchase_growth_project("nature_observation_station")
        self.assertFalse(repeated["success"])
        self.assertEqual(self.engine.state.balance, balance_after_purchase)

    def test_nature_observation_station_purchase_failures_are_atomic(self):
        self._open_management_phase()
        self.engine.state.campsite_star = 3
        self.engine.state.balance = 799
        before_nodes = self.engine.get_growth_progress()["completed_growth_nodes"]
        before_star = self.engine.state.campsite_star

        insufficient = self.engine.purchase_growth_project("nature_observation_station")
        self.assertFalse(insufficient["success"])
        self.assertEqual(self.engine.state.balance, 799)
        self.assertFalse(self.engine.state.nature_observation_station_built)

        self.engine.state.balance = 800
        self.engine.state.turn = 5
        wrong_turn = self.engine.purchase_growth_project("nature_observation_station")
        self.assertFalse(wrong_turn["success"])
        self.assertEqual(self.engine.state.balance, 800)
        self.assertFalse(self.engine.state.nature_observation_station_built)
        self.assertEqual(self.engine.state.campsite_star, before_star)
        self.assertEqual(
            self.engine.get_growth_progress()["completed_growth_nodes"], before_nodes
        )

    def test_hot_spring_requires_four_star_only(self):
        self._open_management_phase()
        project = self._catalog()["hot_spring"]
        self.assertTrue(project["prerequisite_met"])
        self.assertFalse(project["operation_requirement_met"])
        self.assertIn("campsite_star_required", project["unmet_conditions"])

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

    def test_later_tents_require_their_star_and_previous_tent(self):
        self._open_management_phase()
        project = self._catalog()["tent_3"]
        self.assertFalse(project["operation_requirement_met"])
        self.assertFalse(project["prerequisite_met"])
        self.assertIn("campsite_star_required", project["unmet_conditions"])
        self.assertIn("previous_tent_required", project["unmet_conditions"])

        self.engine.tents[2].is_unlocked = True
        self.engine.state.campsite_star = 2
        project = self._catalog()["tent_3"]
        self.assertTrue(project["can_purchase_now"])
        self.assertEqual(project["progress"]["required_campsite_star"], 2)

        requirements = {"tent_4": (3, 3), "tent_5": (4, 4), "tent_6": (5, 5)}
        for project_id, (previous_tent, required_star) in requirements.items():
            self.engine.tents[previous_tent].is_unlocked = False
            self.engine.state.campsite_star = required_star
            self.assertFalse(self._catalog()[project_id]["prerequisite_met"])
            self.engine.tents[previous_tent].is_unlocked = True
            self.assertTrue(self._catalog()[project_id]["operation_requirement_met"])

    def test_facility_levels_require_their_star_and_previous_level(self):
        self._open_management_phase()
        project = self._catalog()["dining_lv1"]
        self.assertFalse(project["can_purchase_now"])
        self.engine.state.campsite_star = 2
        self.assertTrue(self._catalog()["dining_lv1"]["can_purchase_now"])
        self.assertFalse(self._catalog()["dining_lv2"]["prerequisite_met"])

        self.engine.facilities["dining"].level = 1
        project = self._catalog()["dining_lv1"]
        self.assertTrue(project["completed"])
        self.assertFalse(project["can_purchase_now"])
        self.assertIn("already_completed", project["unmet_conditions"])

        self.engine.state.campsite_star = 2
        project = self._catalog()["dining_lv2"]
        self.assertFalse(project["operation_requirement_met"])
        self.engine.state.campsite_star = 3
        self.assertTrue(self._catalog()["dining_lv2"]["can_purchase_now"])

    def test_entertainment_and_greenery_levels_use_their_star_requirements(self):
        self._open_management_phase()
        self.engine.state.campsite_star = 2
        self.assertTrue(self._catalog()["entertainment_lv1"]["can_purchase_now"])
        self.engine.facilities["entertainment"].level = 1
        self.engine.state.campsite_star = 2
        self.assertFalse(self._catalog()["entertainment_lv2"]["operation_requirement_met"])
        self.engine.state.campsite_star = 3
        self.assertTrue(self._catalog()["entertainment_lv2"]["can_purchase_now"])

        self.engine.state.campsite_star = 2
        self.assertTrue(self._catalog()["greenery_lv1"]["can_purchase_now"])
        self.engine.facilities["greenery"].level = 1
        self.engine.state.campsite_star = 2
        self.assertFalse(self._catalog()["greenery_lv2"]["operation_requirement_met"])
        self.engine.state.campsite_star = 3
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
