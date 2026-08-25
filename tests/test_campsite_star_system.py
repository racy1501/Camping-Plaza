import json
import os
import sqlite3
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class CampsiteStarSystemTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "campsite_star.sqlite")
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

    def _set_growth_nodes(self, count):
        for tent_id in range(2, 7):
            self.engine.tents[tent_id].is_unlocked = False
        for facility in self.engine.facilities.values():
            facility.level = 0

        for tent_id in range(2, min(7, count + 2)):
            self.engine.tents[tent_id].is_unlocked = True
        remaining = count - sum(
            self.engine.tents[tent_id].is_unlocked for tent_id in range(2, 7)
        )
        for facility_name in ("dining", "entertainment", "greenery"):
            level = min(2, max(0, remaining))
            self.engine.facilities[facility_name].level = level
            remaining -= level

    def _set_star_conditions(self, *, served, nodes, rating=None, hot_spring=False):
        self.engine.state.total_served_groups = served
        self._set_growth_nodes(nodes)
        self.engine.state.historical_highest_rating = rating
        self.engine.state.hot_spring_built = hot_spring

    def _restore_legacy_snapshot(self, *, remove_served_groups=False):
        self.assertTrue(self.engine.save_state())
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = ?",
                ("local-default",),
            ).fetchone()[0]
            payload = json.loads(raw)
            payload["state"].pop("campsite_star", None)
            payload["state"].pop("historical_highest_rating", None)
            if remove_served_groups:
                payload["state"].pop("total_served_groups", None)
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ? WHERE session_id = ?",
                (json.dumps(payload), "local-default"),
            )
            conn.commit()
        finally:
            conn.close()
        return CampingPlazaEngine(db_path=self.db_path)

    def _finish_day_and_start_next(self):
        self.assertTrue(self.engine.submit_day_end_actions([])["success"])
        self.assertTrue(self.engine.start_next_day()["success"])

    def test_new_game_defaults_to_one_star(self):
        self.assertEqual(self.engine.state.campsite_star, 1)
        self.assertIsNone(self.engine.state.historical_highest_rating)
        self.assertEqual(self.engine.get_campsite_star_progress()["next_star"], 2)

    def test_next_star_exposes_each_condition_progress(self):
        self._set_star_conditions(served=52, nodes=5, rating=4.0)

        progress = self.engine.get_campsite_star_progress()

        self.assertEqual(progress["current_star"], 1)
        self.assertEqual(progress["next_star"], 2)
        self.assertEqual(
            set(progress["conditions"]), {"served_groups", "growth_nodes"}
        )

        self.engine.state.campsite_star = 2
        progress = self.engine.get_campsite_star_progress()
        self.assertEqual(progress["next_star"], 3)
        self.assertEqual(
            progress["conditions"],
            {
                "served_groups": {"current": 52, "required": 45, "met": True},
                "growth_nodes": {"current": 5, "required": 5, "met": True},
                "historical_rating": {"current": 4.0, "required": 4.1, "met": False},
            },
        )
        self.assertFalse(progress["requirement_met"])

    def test_star_progress_has_hot_spring_only_for_five_star(self):
        self._set_star_conditions(served=90, nodes=9, rating=4.3)
        self.engine.state.campsite_star = 4
        progress = self.engine.get_campsite_star_progress()
        self.assertEqual(
            set(progress["conditions"]),
            {"served_groups", "growth_nodes", "historical_rating", "hot_spring_built"},
        )

        self.engine.state.campsite_star = 5
        max_progress = self.engine.get_campsite_star_progress()
        self.assertEqual(max_progress["current_star"], 5)
        self.assertIsNone(max_progress["next_star"])
        self.assertTrue(max_progress["is_max_star"])
        self.assertTrue(max_progress["requirement_met"])
        self.assertFalse(max_progress["pending_morning_upgrade"])
        self.assertNotIn("conditions", max_progress)

    def test_insufficient_conditions_do_not_upgrade(self):
        self._set_star_conditions(served=14, nodes=10, rating=5.0)

        self.assertFalse(self.engine._update_campsite_star())
        self.assertEqual(self.engine.state.campsite_star, 1)

    def test_sequential_upgrade_reaches_each_star_without_skipping(self):
        self._set_star_conditions(served=150, nodes=10, rating=4.6)

        self.assertTrue(self.engine._update_campsite_star())
        self.assertEqual(self.engine.state.campsite_star, 4)

        self.engine.state.hot_spring_built = True
        self.assertTrue(self.engine._update_campsite_star())
        self.assertEqual(self.engine.state.campsite_star, 5)

    def test_each_star_uses_its_confirmed_threshold(self):
        self._set_star_conditions(served=15, nodes=1)
        self.engine._update_campsite_star()
        self.assertEqual(self.engine.state.campsite_star, 2)

        self._set_star_conditions(served=45, nodes=5, rating=4.1)
        self.engine._update_campsite_star()
        self.assertEqual(self.engine.state.campsite_star, 3)

        self._set_star_conditions(served=90, nodes=9, rating=4.3)
        self.engine._update_campsite_star()
        self.assertEqual(self.engine.state.campsite_star, 4)

        self._set_star_conditions(served=150, nodes=10, rating=4.6, hot_spring=True)
        self.engine._update_campsite_star()
        self.assertEqual(self.engine.state.campsite_star, 5)

    def test_current_rating_fall_does_not_reduce_star(self):
        self._set_star_conditions(served=150, nodes=10, rating=4.6, hot_spring=True)
        self.engine._update_campsite_star()
        self.engine.state.review_history = [{"rating": 1}] * 20

        self.assertEqual(self.engine.get_average_rating(), 1.0)
        self.assertFalse(self.engine._update_campsite_star())
        self.assertEqual(self.engine.state.campsite_star, 5)

    def test_pending_morning_upgrade_is_derived_from_next_star_conditions(self):
        self._set_star_conditions(served=15, nodes=1)

        pending = self.engine.get_campsite_star_progress()
        self.assertTrue(pending["requirement_met"])
        self.assertTrue(pending["pending_morning_upgrade"])

        self._set_star_conditions(served=14, nodes=1)
        self.assertFalse(
            self.engine.get_campsite_star_progress()["pending_morning_upgrade"]
        )

    def test_served_groups_upgrade_only_on_next_morning(self):
        self._set_star_conditions(served=14, nodes=1)
        self.engine.state.day = 2
        self.engine.state.turn = 6

        self.engine._record_served_group_once(
            NPCGroup(id=901, group_size=1, visit_type="day")
        )

        self.assertEqual(self.engine.state.campsite_star, 1)
        self.assertTrue(
            self.engine.get_campsite_star_progress()["pending_morning_upgrade"]
        )
        self._finish_day_and_start_next()
        self.assertEqual(self.engine.state.campsite_star, 2)

    def test_growth_purchase_waits_until_next_morning_to_unlock_next_star_projects(self):
        self.engine.state.campsite_star = 2
        self.engine.state.total_served_groups = 45
        self.engine.state.historical_highest_rating = 4.1
        self.engine.state.day = 2
        self.engine.state.turn = 6
        self.engine.state.balance = 5000
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[3].is_unlocked = True
        self.engine.facilities["dining"].level = 1
        self.engine.facilities["entertainment"].level = 1

        self.assertTrue(
            self.engine.purchase_growth_project("greenery_lv1")["success"]
        )
        self.assertEqual(self.engine.state.campsite_star, 2)
        self.assertTrue(
            self.engine.get_campsite_star_progress()["pending_morning_upgrade"]
        )

        blocked = self.engine.purchase_growth_project("tent_4")
        self.assertFalse(blocked["success"])
        self.assertIn("campsite_star_required", blocked["unmet_conditions"])

        self._finish_day_and_start_next()
        self.assertEqual(self.engine.state.campsite_star, 3)
        next_progress = self.engine.get_campsite_star_progress()
        self.assertEqual(next_progress["next_star"], 4)
        self.assertFalse(next_progress["pending_morning_upgrade"])
        self.engine.state.turn = 6
        self.assertTrue(self.engine.purchase_growth_project("tent_4")["success"])

    def test_hot_spring_completion_upgrades_to_five_stars_next_morning(self):
        self._set_star_conditions(served=150, nodes=10, rating=4.6)
        self.engine.state.campsite_star = 4
        self.engine.state.day = 2
        self.engine.state.turn = 6
        self.engine.state.balance = 3000

        self.assertTrue(self.engine.purchase_growth_project("hot_spring")["success"])
        self.assertEqual(self.engine.state.campsite_star, 4)
        self.assertTrue(
            self.engine.get_campsite_star_progress()["pending_morning_upgrade"]
        )
        self._finish_day_and_start_next()
        self.assertEqual(self.engine.state.campsite_star, 5)
        self.assertFalse(
            self.engine.get_campsite_star_progress()["pending_morning_upgrade"]
        )

    def test_morning_review_settlement_updates_rating_then_upgrades_once(self):
        self._set_star_conditions(served=90, nodes=9)
        self.engine.state.campsite_star = 3
        self.engine.state.day = 2
        self.engine.state.turn = 6
        self.engine.state.pending_reviews = [{"created_day": 2, "rating": 5}]

        self._finish_day_and_start_next()
        self.assertEqual(self.engine.state.historical_highest_rating, 5.0)
        self.assertEqual(self.engine.state.campsite_star, 4)

    def test_hot_spring_requires_four_stars_and_still_costs_3000(self):
        self._set_star_conditions(served=90, nodes=9, rating=4.3)
        self.engine.state.turn = 6
        self.engine.state.balance = 3000
        self.engine._update_campsite_star()
        self.engine.state.campsite_star = 3

        blocked = self.engine.purchase_growth_project("hot_spring")
        self.assertFalse(blocked["success"])
        self.assertIn("campsite_star_required", blocked["unmet_conditions"])

        self.engine.state.campsite_star = 4
        purchased = self.engine.purchase_growth_project("hot_spring")
        self.assertTrue(purchased["success"])
        self.assertEqual(purchased["price"], 3000)
        self.assertEqual(self.engine.state.balance, 0)
        self.assertTrue(self.engine.state.hot_spring_built)

    def test_five_stars_requires_built_hot_spring(self):
        self._set_star_conditions(served=150, nodes=10, rating=4.6)

        self.engine._update_campsite_star()
        self.assertEqual(self.engine.state.campsite_star, 4)

    def test_legacy_high_progress_snapshot_preserves_state_and_restores_four_stars(self):
        self._set_star_conditions(served=90, nodes=9)
        self.engine.state.turn = 6
        self.engine.state.balance = 4321
        self.engine.state.review_history = [{"rating": 5}] * 20
        self.engine.state.total_reviews = 20
        self.engine.state.total_rating_sum = 100
        self.engine.npc_pool.append(NPCGroup(id=700, group_size=2, visit_type="day"))

        restored = self._restore_legacy_snapshot()

        self.assertEqual(restored.state.campsite_star, 4)
        self.assertEqual(restored.state.historical_highest_rating, 5.0)
        self.assertEqual(restored.state.balance, 4321)
        self.assertTrue(all(restored.tents[tent_id].is_unlocked for tent_id in range(2, 7)))
        self.assertEqual(restored.facilities["dining"].level, 2)
        self.assertEqual(restored.facilities["entertainment"].level, 2)
        self.assertEqual(restored.npc_pool[0].id, 700)
        self.assertEqual(restored.state.review_history, [{"rating": 5}] * 20)
        hot_spring = next(
            project for project in restored.get_growth_project_catalog()
            if project["project_id"] == "hot_spring"
        )
        self.assertTrue(hot_spring["can_purchase_now"])

    def test_legacy_snapshot_restores_five_stars_when_hot_spring_is_built(self):
        self._set_star_conditions(served=150, nodes=10, hot_spring=True)
        self.engine.state.review_history = [{"rating": 5}] * 20

        restored = self._restore_legacy_snapshot()

        self.assertEqual(restored.state.campsite_star, 5)
        self.assertEqual(restored.state.historical_highest_rating, 5.0)

    def test_legacy_snapshot_with_insufficient_conditions_is_not_promoted(self):
        self._set_star_conditions(served=14, nodes=10)
        self.engine.state.review_history = [{"rating": 5}] * 20

        restored = self._restore_legacy_snapshot()

        self.assertEqual(restored.state.campsite_star, 1)
        self.assertEqual(restored.state.historical_highest_rating, 5.0)

    def test_legacy_snapshot_uses_achievement_lower_bound_when_served_count_is_missing(self):
        self._set_star_conditions(served=0, nodes=9)
        self.engine.state.unlocked_achievement_ids = ["served_groups_100"]
        self.engine.state.review_history = [{"rating": 5}] * 20

        restored = self._restore_legacy_snapshot(remove_served_groups=True)

        self.assertEqual(restored.state.total_served_groups, 100)
        self.assertEqual(restored.state.campsite_star, 4)

    def test_existing_star_fields_are_not_recalculated_on_restore(self):
        self._set_star_conditions(served=150, nodes=10, rating=4.6, hot_spring=True)
        self.engine.state.campsite_star = 3
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.campsite_star, 3)
        self.assertEqual(restored.state.historical_highest_rating, 4.6)

    def test_legacy_snapshot_can_continue_conflict_growth_and_review_workflows(self):
        self.engine.state.turn = 3
        self.engine.state.balance = 1000
        self.engine.npc_pool.extend([
            NPCGroup(id=801, group_size=1, visit_type="day", campsite_slot=1),
            NPCGroup(id=802, group_size=1, visit_type="day", campsite_slot=2),
        ])
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 801, "npc_b_id": 802,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }
        restored = self._restore_legacy_snapshot()

        self.assertTrue(restored.resolve_current_temporary_conflict("verbal")["success"])
        restored.state.turn = 6
        restored.state.day = 2
        restored.state.balance = 600
        self.assertTrue(restored.purchase_growth_project("tent_2")["success"])
        restored.state.day = 3
        restored.state.pending_reviews = [{"created_day": 2, "rating": 5}]
        restored._settle_pending_reviews({"events": []})
        self.assertEqual(restored.state.review_history[-1]["rating"], 5)

    def test_star_state_survives_snapshot_restore(self):
        self._set_star_conditions(served=90, nodes=9, rating=4.3)
        self.engine._update_campsite_star()
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.campsite_star, 4)
        self.assertEqual(restored.state.historical_highest_rating, 4.3)

    def test_existing_tent_growth_purchase_remains_available_without_star(self):
        self.engine.state.turn = 6
        self.engine.state.day = 2
        self.engine.state.balance = 600

        result = self.engine.purchase_growth_project("tent_2")

        self.assertTrue(result["success"])
        self.assertTrue(self.engine.tents[2].is_unlocked)
        self.assertEqual(self.engine.state.campsite_star, 1)

    def test_existing_high_tier_projects_are_not_reverted_below_unlock_star(self):
        self.engine.state.campsite_star = 1
        self.engine.tents[6].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.facilities["greenery"].level = 2
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        catalog = {
            project["project_id"]: project
            for project in restored.get_growth_project_catalog()
        }

        self.assertEqual(restored.state.campsite_star, 1)
        self.assertTrue(restored.tents[6].is_unlocked)
        self.assertEqual(restored.facilities["dining"].level, 2)
        self.assertTrue(catalog["tent_6"]["completed"])
        self.assertTrue(catalog["dining_lv2"]["completed"])
        self.assertTrue(catalog["entertainment_lv2"]["completed"])
        self.assertTrue(catalog["greenery_lv2"]["completed"])
