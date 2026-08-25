import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class AchievementTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "achievements.sqlite")
        self._extra_paths = [
            self.db_path,
            os.path.join(self._db_dir, "paid-achievements.sqlite"),
            os.path.join(self._db_dir, "unpaid-achievements.sqlite"),
            *(
                os.path.join(self._db_dir, f"{name}-deadline.sqlite")
                for name in ("paid", "partial", "unpaid")
            ),
        ]
        for path in self._extra_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        self.engine = CampingPlazaEngine(db_path=self.db_path)

    def tearDown(self):
        for path in self._extra_paths:
            try:
                os.remove(path)
            except FileNotFoundError:
                pass

    def _start_next_day(self, *, day=2):
        self.engine.state.day = day
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True
        return self.engine.start_next_day()

    def test_unlock_is_permanent_and_next_turn_one_consumes_pending_once(self):
        self.assertTrue(self.engine._unlock_achievement("first_tip"))
        self.assertFalse(self.engine._unlock_achievement("first_tip"))

        result = self._start_next_day()

        self.assertEqual(
            result["achievement_notifications"],
            [{"id": "first_tip", "name": "还有小费！"}],
        )
        self.assertEqual(self.engine.state.pending_achievement_ids, [])
        self.assertEqual(self.engine.state.unlocked_achievement_ids, ["first_tip"])
        self.engine.state.day_end_completed = True
        next_result = self.engine.start_next_day()
        self.assertNotIn("achievement_notifications", next_result)

    def test_multiple_pending_achievements_are_broadcast_together(self):
        self.engine._unlock_achievement("first_tip")
        self.engine._unlock_achievement("tent_2_purchased")

        result = self._start_next_day()

        self.assertEqual(
            [item["id"] for item in result["achievement_notifications"]],
            ["first_tip", "tent_2_purchased"],
        )

    def test_full_state_exposes_persistent_achievement_state(self):
        self.engine._unlock_achievement("first_tip")

        achievements = self.engine.get_full_state()["achievements"]

        self.assertEqual(
            achievements["unlocked"],
            [{"id": "first_tip", "name": "还有小费！"}],
        )
        self.assertEqual(achievements["pending"], achievements["unlocked"])

    def test_first_day_achievement_is_queued_at_day_two_boundary(self):
        self.engine.state.day = 1
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True

        result = self.engine.start_next_day()

        self.assertEqual(self.engine.state.day, 2)
        self.assertEqual(
            [item["id"] for item in result["achievement_notifications"]],
            ["first_day_complete"],
        )

    def test_legacy_snapshot_without_achievement_fields_loads_empty(self):
        self.engine._unlock_achievement("first_tip")
        self.assertTrue(self.engine.save_state())
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = ?",
                (self.engine.session_id,),
            ).fetchone()[0]
            payload = json.loads(raw)
            payload["state"].pop("unlocked_achievement_ids")
            payload["state"].pop("pending_achievement_ids")
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ? WHERE session_id = ?",
                (json.dumps(payload, ensure_ascii=False), self.engine.session_id),
            )
            conn.commit()
        finally:
            conn.close()

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.unlocked_achievement_ids, [])
        self.assertEqual(restored.state.pending_achievement_ids, [])

    def test_served_group_threshold_achievements(self):
        for index in range(1, 151):
            npc = NPCGroup(id=index, group_size=1, visit_type="day")
            self.engine._record_served_group_once(npc)

        self.assertEqual(self.engine.state.total_served_groups, 150)
        self.assertTrue({
            "first_served_group", "served_groups_50", "served_groups_100",
            "served_groups_150",
        }.issubset(self.engine.state.unlocked_achievement_ids))

    def test_debt_deadline_achievements_are_mutually_exclusive(self):
        paid_engine = CampingPlazaEngine(
            db_path=os.path.join(self._db_dir, "paid-achievements.sqlite")
        )
        paid_engine.state.day = 25
        paid_engine.state.turn = 6
        paid_engine.state.day_end_completed = True
        paid_engine.state.debt_remaining = 0
        paid_result = paid_engine.start_next_day()
        self.assertEqual(
            [item["id"] for item in paid_result["achievement_notifications"]],
            ["debt_paid_by_deadline"],
        )

        unpaid_engine = CampingPlazaEngine(
            db_path=os.path.join(self._db_dir, "unpaid-achievements.sqlite")
        )
        unpaid_engine.state.day = 25
        unpaid_engine.state.turn = 6
        unpaid_engine.state.day_end_completed = True
        unpaid_engine.state.debt_remaining = 1
        unpaid_result = unpaid_engine.start_next_day()
        self.assertEqual(
            [item["id"] for item in unpaid_result["achievement_notifications"]],
            ["debt_unpaid_by_deadline"],
        )

    def test_day_25_repayment_results_determine_the_day_26_achievement(self):
        cases = (
            ("paid", 21000, "debt_paid_by_deadline"),
            ("partial", 2000, "debt_unpaid_by_deadline"),
            ("unpaid", None, "debt_unpaid_by_deadline"),
        )
        for name, amount, expected_achievement in cases:
            with self.subTest(name=name):
                engine = CampingPlazaEngine(
                    db_path=os.path.join(self._db_dir, f"{name}-deadline.sqlite")
                )
                engine.state.day = 25
                engine.state.turn = 6
                engine.state.balance = 21000
                actions = [] if amount is None else [{
                    "action": "repay_debt", "params": {"amount": amount},
                }]

                day_end = engine.submit_day_end_actions(actions)
                result = engine.start_next_day()

                self.assertTrue(day_end["success"])
                self.assertEqual(engine.state.day, 26)
                self.assertEqual(
                    [item["id"] for item in result["achievement_notifications"]],
                    [expected_achievement],
                )
                self.assertEqual(
                    set(engine.state.unlocked_achievement_ids)
                    & engine.DEBT_RESULT_ACHIEVEMENT_IDS,
                    {expected_achievement},
                )

    def test_hot_spring_and_normal_growth_achievements(self):
        self.engine.state.turn = 6
        self.engine.state.day = 12
        self.engine.state.balance = 10000
        self.engine.state.total_served_groups = 75
        for tent_id in range(2, 7):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.facilities["greenery"].level = 2

        result = self.engine.purchase_growth_project("hot_spring")

        self.assertTrue(result["success"])
        self.assertTrue({
            "hot_spring_built", "all_tents_unlocked",
            "all_normal_growth_complete",
        }.issubset(self.engine.state.unlocked_achievement_ids))

    def test_tent_and_facility_level_one_achievements(self):
        self.engine.state.turn = 6
        self.engine.state.day = 2
        self.engine.state.balance = 100000
        self.engine.state.successful_dining_groups = 8
        self.engine.state.successful_paid_entertainment_groups = 8
        self.engine.state.successful_greenery_maintenance_count = 4

        for project_id, achievement_id in (
            ("tent_2", "tent_2_purchased"),
            ("dining_lv1", "dining_lv1"),
            ("entertainment_lv1", "entertainment_lv1"),
            ("greenery_lv1", "greenery_lv1"),
        ):
            self.assertTrue(self.engine.purchase_growth_project(project_id)["success"])
            self.assertIn(achievement_id, self.engine.state.unlocked_achievement_ids)

    def test_first_overnight_day_to_overnight_and_tip_achievements(self):
        self.engine.state.turn = 2
        overnight = NPCGroup(id=1, group_size=1, visit_type="overnight")
        self.engine._checkin_npc(overnight, 1, {"events": []})
        self.assertIn("first_overnight_group", self.engine.state.unlocked_achievement_ids)

        self.engine.state.day = 2
        self.engine.state.turn = 4
        day_guest = NPCGroup(id=2, group_size=1, visit_type="day")
        self.engine.npc_pool.append(day_guest)
        self.engine.tents[1].status = "available"
        self.engine.tents[1].occupied_by = None
        self.engine.state.today_arrival_plan = [{
            "planned_day": 2,
            "npc_id": 2,
            "visit_type": "day",
            "day_to_overnight_intent": True,
        }]
        self.engine._process_day_to_overnight({"events": []})
        self.assertIn("first_day_to_overnight", self.engine.state.unlocked_achievement_ids)

        with mock.patch("game_engine.random.random", return_value=0.0):
            self.engine._settle_tips({"events": []})
        self.assertIn("first_tip", self.engine.state.unlocked_achievement_ids)


if __name__ == "__main__":
    unittest.main()
