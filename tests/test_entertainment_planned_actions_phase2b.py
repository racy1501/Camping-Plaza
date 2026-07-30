import os
import sys
import uuid
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


def _temp_db_path() -> str:
    return os.path.join(
        _PROJECT_ROOT, f".entertainment_phase2b_{uuid.uuid4().hex}.sqlite"
    )


def _cleanup_db_path(db_path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = db_path + suffix if suffix else db_path
        if os.path.exists(path):
            os.remove(path)


class EntertainmentPlannedActionsPhase2BTests(unittest.TestCase):
    def _new_engine(self):
        db_path = _temp_db_path()
        self.addCleanup(_cleanup_db_path, db_path)
        engine = CampingPlazaEngine(db_path=db_path)
        engine.state.today_arrival_plan_day = 0
        engine.state.today_arrival_plan = []
        return engine

    def _make_guest(
        self,
        npc_id: int,
        *,
        visit_type: str = "day",
        economic_level: int = 1,
        spending_habit: int = 1,
        total_satisfaction: int = 60,
    ):
        npc = NPCGroup(
            id=npc_id,
            group_size=2,
            visit_type=visit_type,
            total_satisfaction=total_satisfaction,
        )
        npc.economic_level = economic_level
        npc.spending_habit = spending_habit
        npc.temperament = 0
        return npc

    def _make_entry(
        self,
        engine,
        npc,
        *,
        arrival_turn: int,
        source: str = "natural_day",
        arrival_status: str = "pending",
    ):
        entry = engine._build_arrival_plan_entry(npc, arrival_turn, source)
        entry["arrival_status"] = arrival_status
        return entry

    def test_dining_probability_uses_direct_table_without_multiplier(self):
        engine = self._new_engine()
        cases = (
            (0, 0.54, True),
            (0, 0.55, False),
            (1, 0.69, True),
            (1, 0.70, False),
            (2, 0.84, True),
            (2, 0.85, False),
        )

        for spending_habit, roll, should_exist in cases:
            with self.subTest(spending_habit=spending_habit, roll=roll):
                npc = self._make_guest(100 + spending_habit, spending_habit=spending_habit)
                entry = self._make_entry(engine, npc, arrival_turn=2)
                with mock.patch.object(
                    engine,
                    "_calc_spend_probability",
                    side_effect=AssertionError("should not use base probability multiplier"),
                ):
                    with mock.patch("game_engine.random.random", return_value=roll):
                        action = engine._build_dining_planned_action(entry)
                self.assertEqual(action is not None, should_exist)

    def test_paid_entertainment_probability_uses_direct_table(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        cases = (
            (0, 0.29, True),
            (0, 0.30, False),
            (1, 0.49, True),
            (1, 0.50, False),
            (2, 0.69, True),
            (2, 0.70, False),
        )

        for spending_habit, roll, should_exist in cases:
            with self.subTest(spending_habit=spending_habit, roll=roll):
                npc = self._make_guest(200 + spending_habit, spending_habit=spending_habit)
                entry = self._make_entry(engine, npc, arrival_turn=2)
                with mock.patch.object(
                    engine,
                    "_calc_spend_probability",
                    side_effect=AssertionError("should not use base probability multiplier"),
                ):
                    with mock.patch("game_engine.random.random", return_value=roll):
                        action = engine._build_paid_entertainment_planned_action(entry)
                self.assertEqual(action is not None, should_exist)

    def test_free_entertainment_probability_is_fixed_at_fifty_percent(self):
        engine = self._new_engine()

        with mock.patch("game_engine.random.random", return_value=0.49):
            self.assertIsNotNone(engine._build_free_entertainment_planned_action())
        with mock.patch("game_engine.random.random", return_value=0.50):
            self.assertIsNone(engine._build_free_entertainment_planned_action())

    def test_new_game_entertainment_defaults_to_lv0(self):
        engine = self._new_engine()
        self.assertEqual(engine.facilities["entertainment"].level, 0)

    def test_default_new_game_skips_paid_entertainment_roll_and_action(self):
        engine = self._new_engine()
        npc = self._make_guest(250, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch.object(
            engine,
            "_build_paid_entertainment_planned_action",
            side_effect=AssertionError("lv0 should not call paid entertainment roll"),
        ) as paid_builder_mock:
            with mock.patch(
                "game_engine.random.random", side_effect=[0.99, 0.00]
            ) as random_mock:
                with mock.patch("game_engine.random.sample", return_value=[4]):
                    engine._append_planned_actions(entry)

        paid_builder_mock.assert_not_called()
        self.assertEqual(random_mock.call_count, 2)
        self.assertEqual(
            [action["action"] for action in entry["planned_actions"]],
            ["free_entertainment"],
        )

    def test_default_new_game_free_entertainment_still_generates_normally(self):
        engine = self._new_engine()
        npc = self._make_guest(260, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=4)

        with mock.patch("game_engine.random.random", side_effect=[0.99, 0.00]):
            with mock.patch("game_engine.random.sample", return_value=[4]):
                engine._append_planned_actions(entry)

        self.assertEqual(len(entry["planned_actions"]), 1)
        self.assertEqual(entry["planned_actions"][0]["action"], "free_entertainment")
        self.assertEqual(entry["planned_actions"][0]["planned_turn"], 4)

    def test_lv1_paid_and_free_entertainment_can_both_exist(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(270, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=4)

        with mock.patch("game_engine.random.random", side_effect=[0.99, 0.00, 0.00]):
            with mock.patch("game_engine.random.sample", return_value=[4, 5]):
                engine._append_planned_actions(entry)

        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"paid_entertainment", "free_entertainment"},
        )

    def test_upgrade_to_lv1_reenables_paid_entertainment_planning(self):
        engine = self._new_engine()
        engine.state.turn = 6
        engine.state.balance = 99999

        upgrade_result = engine.upgrade_facility("entertainment")

        self.assertTrue(upgrade_result["success"])
        self.assertEqual(engine.facilities["entertainment"].level, 1)
        npc = self._make_guest(280, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=3)
        with mock.patch("game_engine.random.random", side_effect=[0.99, 0.00, 0.99]):
            with mock.patch("game_engine.random.sample", return_value=[3]):
                engine._append_planned_actions(entry)
        self.assertEqual(
            [action["action"] for action in entry["planned_actions"]],
            ["paid_entertainment"],
        )

    def test_saved_lv1_entertainment_level_restores_for_existing_save(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=engine.db_path)

        self.assertEqual(restored.facilities["entertainment"].level, 1)

    def test_three_planned_actions_roll_independently(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(301, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch(
            "game_engine.random.random", side_effect=[0.60, 0.60, 0.40]
        ) as random_mock:
            with mock.patch("game_engine.random.sample", return_value=[3, 5]):
                engine._append_planned_actions(entry)

        self.assertEqual(random_mock.call_count, 3)
        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"dining", "free_entertainment"},
        )

    def test_all_three_rolls_can_fail_and_leave_empty_actions(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(401, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=2)

        with mock.patch(
            "game_engine.random.random", side_effect=[0.99, 0.99, 0.99]
        ):
            with mock.patch("game_engine.random.sample") as sample_mock:
                engine._append_planned_actions(entry)

        self.assertEqual(entry["planned_actions"], [])
        sample_mock.assert_not_called()

    def test_only_free_entertainment_can_be_generated(self):
        engine = self._new_engine()
        npc = self._make_guest(501, spending_habit=0)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch("game_engine.random.random", side_effect=[0.99, 0.00]):
            with mock.patch("game_engine.random.sample", return_value=[5]):
                engine._append_planned_actions(entry)

        self.assertEqual(len(entry["planned_actions"]), 1)
        self.assertEqual(entry["planned_actions"][0]["action"], "free_entertainment")
        self.assertEqual(entry["planned_actions"][0]["planned_turn"], 5)
        self.assertEqual(entry["planned_actions"][0]["status"], "pending")

    def test_paid_entertainment_success_creates_paid_action(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(601, spending_habit=0)
        entry = self._make_entry(engine, npc, arrival_turn=4)

        with mock.patch(
            "game_engine.random.random", side_effect=[0.99, 0.00, 0.99]
        ):
            with mock.patch("game_engine.random.sample", return_value=[4]):
                engine._append_planned_actions(entry)

        self.assertEqual(len(entry["planned_actions"]), 1)
        self.assertEqual(entry["planned_actions"][0]["action"], "paid_entertainment")
        self.assertEqual(entry["planned_actions"][0]["planned_turn"], 4)

    def test_turn_three_arrival_with_all_success_keeps_three_unique_actions(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(701, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch("game_engine.random.random", side_effect=[0.00, 0.00, 0.00]):
            with mock.patch("game_engine.random.sample", return_value=[5, 3, 4]):
                engine._append_planned_actions(entry)

        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"dining", "paid_entertainment", "free_entertainment"},
        )
        turns = [action["planned_turn"] for action in entry["planned_actions"]]
        self.assertEqual(sorted(turns), [3, 4, 5])
        self.assertEqual(len(set(turns)), 3)

    def test_turn_four_arrival_drops_free_entertainment_when_no_slot_remains(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(801, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=4)

        with mock.patch("game_engine.random.random", side_effect=[0.00, 0.00, 0.00]):
            with mock.patch("game_engine.random.sample", return_value=[5, 4]) as sample_mock:
                engine._append_planned_actions(entry)

        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"dining", "paid_entertainment"},
        )
        self.assertEqual(
            {action["planned_turn"] for action in entry["planned_actions"]},
            {4, 5},
        )
        self.assertEqual(sample_mock.call_args.args, ([4, 5], 2))

    def test_same_day_repeated_plan_generation_does_not_reroll(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        engine.state.day = 3
        engine.state.turn = 1
        guest = self._make_guest(901, spending_habit=1)

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ):
            with mock.patch.object(
                CampingPlazaEngine, "_create_day_guest", return_value=guest
            ):
                with mock.patch(
                    "game_engine.random.random", side_effect=[0.00, 0.00, 0.00]
                ) as random_mock:
                    with mock.patch(
                        "game_engine.random.sample", return_value=[2, 4, 5]
                    ) as sample_mock:
                        first = engine._ensure_today_arrival_plan()
                        second = engine._ensure_today_arrival_plan()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(random_mock.call_count, 3)
        sample_mock.assert_called_once_with([2, 3, 4, 5], 3)
        entry = engine.state.today_arrival_plan[0]
        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"dining", "paid_entertainment", "free_entertainment"},
        )


if __name__ == "__main__":
    unittest.main()
