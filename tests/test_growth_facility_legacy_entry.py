import copy
import os
import sys
import tempfile
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class GrowthFacilityLegacyEntryTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "growth_facility_legacy.sqlite")
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

    def _open_management_phase(self, *, balance=5000):
        self.engine.state.turn = 6
        self.engine.state.turn_settled = False
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

    def test_dining_uses_growth_price_and_keeps_hidden_fields(self):
        self._open_management_phase(balance=1000)
        self.engine.state.successful_dining_groups = 8
        dining = self.engine.facilities["dining"]
        hidden_before = (
            dining.dining_spend_probability,
            dining.dining_income_multiplier,
            dining.dining_satisfaction,
        )

        result = self.engine.upgrade_facility("dining")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 700)
        self.assertEqual(self.engine.state.balance, 300)
        self.assertEqual(dining.level, 1)
        self.assertEqual(result["message"], "dining升级到Lv.1")
        self.assertEqual(
            (
                dining.dining_spend_probability,
                dining.dining_income_multiplier,
                dining.dining_satisfaction,
            ),
            hidden_before,
        )

    def test_entertainment_uses_growth_price_and_keeps_hidden_fields(self):
        self._open_management_phase(balance=1000)
        self.engine.state.successful_paid_entertainment_groups = 8
        entertainment = self.engine.facilities["entertainment"]
        hidden_before = (
            entertainment.entertainment_satisfaction,
            entertainment.entertainment_income_multiplier,
        )

        result = self.engine.upgrade_facility("entertainment")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 600)
        self.assertEqual(self.engine.state.balance, 400)
        self.assertEqual(entertainment.level, 1)
        self.assertEqual(
            (
                entertainment.entertainment_satisfaction,
                entertainment.entertainment_income_multiplier,
            ),
            hidden_before,
        )

    def test_greenery_uses_growth_effects_without_daily_maintenance_count(self):
        self._open_management_phase(balance=1000)
        greenery = self.engine.facilities["greenery"]
        greenery.greenery_satisfaction = 4.0
        decay_before = greenery.greenery_decay_rate
        self.engine.state.successful_greenery_maintenance_count = 4

        result = self.engine.upgrade_facility("greenery")

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 600)
        self.assertEqual(self.engine.state.balance, 400)
        self.assertEqual(greenery.level, 1)
        self.assertEqual(greenery.greenery_satisfaction, 6.0)
        self.assertTrue(self.engine.state.greenery_processed_today)
        self.assertEqual(self.engine.state.successful_greenery_maintenance_count, 4)
        self.assertEqual(greenery.greenery_decay_rate, decay_before)

    def test_broken_tent_no_longer_blocks_growth_purchase(self):
        self._open_management_phase()
        self.engine.state.successful_dining_groups = 8
        self.engine.tents[1].status = "broken"

        result = self.engine.upgrade_facility("dining")

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.facilities["dining"].level, 1)

    def test_unmet_growth_requirement_keeps_structured_failure_and_state(self):
        self._open_management_phase()
        before = self._snapshot()

        result = self.engine.upgrade_facility("dining")

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "growth_project_not_purchasable")
        self.assertIn("successful_dining_required", result["unmet_conditions"])
        self.assertEqual(result["message"], "当前无法升级该设施")
        self._assert_snapshot_unchanged(before)

    def test_level_two_stops_without_requesting_level_three_project(self):
        self._open_management_phase()
        self.engine.facilities["dining"].level = 2
        before = self._snapshot()

        with mock.patch.object(self.engine, "purchase_growth_project") as purchase_mock:
            result = self.engine.upgrade_facility("dining")

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "无法升级")
        purchase_mock.assert_not_called()
        self._assert_snapshot_unchanged(before)

    def test_missing_facility_keeps_legacy_message_and_state(self):
        before = self._snapshot()

        result = self.engine.upgrade_facility("missing")

        self.assertFalse(result["success"])
        self.assertEqual(result["message"], "设施不存在")
        self._assert_snapshot_unchanged(before)

    def test_same_turn_can_upgrade_two_levels_with_growth_prices(self):
        self._open_management_phase(balance=3000)
        self.engine.state.successful_dining_groups = 36

        self.assertTrue(self.engine.upgrade_facility("dining")["success"])
        self.assertTrue(self.engine.upgrade_facility("dining")["success"])

        self.assertEqual(self.engine.state.balance, 500)
        self.assertEqual(self.engine.facilities["dining"].level, 2)
        dining = self.engine.facilities["dining"]
        self.assertEqual(dining.dining_spend_probability, 0.6)
        self.assertEqual(dining.dining_income_multiplier, 1.0)
        self.assertEqual(dining.dining_satisfaction, 5.0)


if __name__ == "__main__":
    unittest.main()
