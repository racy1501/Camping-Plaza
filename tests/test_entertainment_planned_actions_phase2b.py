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
            (0, 0.39, True),
            (0, 0.40, False),
            (1, 0.54, True),
            (1, 0.55, False),
            (2, 0.69, True),
            (2, 0.70, False),
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

    def test_paid_entertainment_probability_uses_direct_table_at_lv0(self):
        engine = self._new_engine()
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
                if should_exist:
                    self.assertEqual(action["tier_key"], "basic")

    def test_free_entertainment_probability_is_fixed_at_fifty_percent(self):
        engine = self._new_engine()

        with mock.patch("game_engine.random.random", return_value=0.49):
            self.assertIsNotNone(engine._build_free_entertainment_planned_action())
        with mock.patch("game_engine.random.random", return_value=0.50):
            self.assertIsNone(engine._build_free_entertainment_planned_action())

    def test_new_game_entertainment_defaults_to_lv0(self):
        engine = self._new_engine()
        self.assertEqual(engine.facilities["entertainment"].level, 0)

    def test_lv0_paid_entertainment_hit_can_only_choose_basic(self):
        engine = self._new_engine()
        npc = self._make_guest(250, economic_level=2, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch("game_engine.random.random", return_value=0.00):
            action = engine._build_paid_entertainment_planned_action(entry)

        self.assertEqual(action["tier_key"], "basic")

    def test_lv1_paid_entertainment_pool_contains_only_basic_and_standard(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        npc = self._make_guest(260, economic_level=0, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=2)

        with mock.patch("game_engine.random.random", return_value=0.00):
            with mock.patch("game_engine.random.choices", return_value=["basic"]) as choices_mock:
                action = engine._build_paid_entertainment_planned_action(entry)

        self.assertEqual(action["tier_key"], "basic")
        self.assertEqual(choices_mock.call_args.args[0], ["basic", "standard"])
        self.assertEqual(choices_mock.call_args.kwargs["weights"], [60, 30])

    def test_lv2_paid_entertainment_pool_contains_all_three_tiers(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 2
        npc = self._make_guest(270, economic_level=1, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=2)

        with mock.patch("game_engine.random.random", return_value=0.00):
            with mock.patch(
                "game_engine.random.choices", return_value=["standard"]
            ) as choices_mock:
                action = engine._build_paid_entertainment_planned_action(entry)

        self.assertEqual(action["tier_key"], "standard")
        self.assertEqual(
            choices_mock.call_args.args[0], ["basic", "standard", "premium"]
        )
        self.assertEqual(choices_mock.call_args.kwargs["weights"], [30, 50, 20])

    def test_low_economic_level_can_still_draw_premium_paid_entertainment_at_lv2(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 2
        npc = self._make_guest(280, economic_level=0, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=2)

        with mock.patch("game_engine.random.random", return_value=0.00):
            with mock.patch("game_engine.random.choices", return_value=["premium"]):
                action = engine._build_paid_entertainment_planned_action(entry)

        self.assertEqual(action["tier_key"], "premium")

    def test_high_economic_level_can_still_draw_basic_paid_entertainment_at_lv2(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 2
        npc = self._make_guest(290, economic_level=2, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=2)

        with mock.patch("game_engine.random.random", return_value=0.00):
            with mock.patch("game_engine.random.choices", return_value=["basic"]):
                action = engine._build_paid_entertainment_planned_action(entry)

        self.assertEqual(action["tier_key"], "basic")

    def test_lv0_can_plan_paid_and_free_entertainment_independently(self):
        engine = self._new_engine()
        npc = self._make_guest(300, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch("game_engine.random.random", side_effect=[0.99, 0.00, 0.00]):
            with mock.patch("game_engine.random.choices", return_value=["basic"]):
                with mock.patch("game_engine.random.sample", return_value=[4, 5]):
                    engine._append_planned_actions(entry)

        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"paid_entertainment", "free_entertainment"},
        )
        paid_action = next(
            action for action in entry["planned_actions"]
            if action["action"] == "paid_entertainment"
        )
        self.assertEqual(paid_action["tier_key"], "basic")

    def test_paid_entertainment_action_stores_tier_key_during_daily_plan(self):
        engine = self._new_engine()
        engine.state.day = 3
        engine.state.turn = 1
        guest = self._make_guest(310, spending_habit=1, economic_level=1)

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ):
            with mock.patch.object(
                CampingPlazaEngine, "_create_day_guest", return_value=guest
            ):
                with mock.patch(
                    "game_engine.random.random", side_effect=[0.99, 0.00, 0.99]
                ):
                    with mock.patch(
                        "game_engine.random.choices", return_value=["standard"]
                    ):
                        with mock.patch("game_engine.random.sample", return_value=[4]):
                            self.assertTrue(engine._ensure_today_arrival_plan())

        action = engine.state.today_arrival_plan[0]["planned_actions"][0]
        self.assertEqual(action["action"], "paid_entertainment")
        self.assertEqual(action["tier_key"], "standard")
        self.assertEqual(action["status"], "pending")

    def test_three_planned_rolls_still_run_independently(self):
        engine = self._new_engine()
        npc = self._make_guest(320, spending_habit=1)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch(
            "game_engine.random.random", side_effect=[0.54, 0.60, 0.40]
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
        npc = self._make_guest(330, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=2)

        with mock.patch(
            "game_engine.random.random", side_effect=[0.99, 0.99, 0.99]
        ):
            with mock.patch("game_engine.random.sample") as sample_mock:
                engine._append_planned_actions(entry)

        self.assertEqual(entry["planned_actions"], [])
        sample_mock.assert_not_called()

    def test_turn_three_arrival_with_all_success_keeps_three_unique_actions(self):
        engine = self._new_engine()
        npc = self._make_guest(340, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=3)

        with mock.patch("game_engine.random.random", side_effect=[0.00, 0.00, 0.00]):
            with mock.patch("game_engine.random.choices", return_value=["premium"]):
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
        npc = self._make_guest(350, spending_habit=2)
        entry = self._make_entry(engine, npc, arrival_turn=4)

        with mock.patch("game_engine.random.random", side_effect=[0.00, 0.00, 0.00]):
            with mock.patch("game_engine.random.choices", return_value=["basic"]):
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
        engine.state.day = 3
        engine.state.turn = 1
        guest = self._make_guest(360, spending_habit=1)

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
                        "game_engine.random.choices", return_value=["basic"]
                    ) as choices_mock:
                        with mock.patch(
                            "game_engine.random.sample", return_value=[2, 4, 5]
                        ) as sample_mock:
                            first = engine._ensure_today_arrival_plan()
                            second = engine._ensure_today_arrival_plan()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(random_mock.call_count, 3)
        self.assertEqual(choices_mock.call_count, 2)
        sample_mock.assert_called_once_with([2, 3, 4, 5], 3)
        entry = engine.state.today_arrival_plan[0]
        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"dining", "paid_entertainment", "free_entertainment"},
        )


if __name__ == "__main__":
    unittest.main()
