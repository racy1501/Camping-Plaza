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
        _PROJECT_ROOT, f".entertainment_execution_phase2b_{uuid.uuid4().hex}.sqlite"
    )


def _cleanup_db_path(db_path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = db_path + suffix if suffix else db_path
        if os.path.exists(path):
            os.remove(path)


class EntertainmentExecutionPhase2BTests(unittest.TestCase):
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
        group_size: int = 2,
        economic_level: int = 1,
        total_satisfaction: int = 60,
        location: str = "campsite",
    ):
        npc = NPCGroup(
            id=npc_id,
            group_size=group_size,
            visit_type=visit_type,
            total_satisfaction=total_satisfaction,
            location=location,
        )
        npc.economic_level = economic_level
        npc.spending_habit = 1
        npc.temperament = 0
        return npc

    def _make_entry(
        self,
        engine,
        npc,
        *,
        arrival_turn: int,
        source: str = "natural_day",
        arrival_status: str = "arrived",
    ):
        entry = engine._build_arrival_plan_entry(npc, arrival_turn, source)
        entry["arrival_status"] = arrival_status
        return entry

    def _add_paid_action(
        self,
        entry: dict,
        *,
        planned_turn: int,
        tier_key: str,
        status: str = "pending",
    ):
        entry["planned_actions"].append(
            {
                "action": "paid_entertainment",
                "planned_turn": planned_turn,
                "tier_key": tier_key,
                "status": status,
            }
        )
        return entry["planned_actions"][-1]

    def _add_free_action(
        self,
        entry: dict,
        *,
        planned_turn: int,
        status: str = "pending",
    ):
        entry["planned_actions"].append(
            {
                "action": "free_entertainment",
                "planned_turn": planned_turn,
                "status": status,
            }
        )
        return entry["planned_actions"][-1]

    def test_paid_tier_prices_are_fixed_per_group(self):
        expectations = {
            "basic": 30,
            "standard": 45,
            "premium": 65,
        }

        for tier_key, expected_income in expectations.items():
            with self.subTest(tier_key=tier_key):
                engine = self._new_engine()
                engine.state.day = 5
                engine.state.turn = 3
                npc = self._make_guest(100, group_size=5, total_satisfaction=50)
                engine.npc_pool.append(npc)
                entry = self._make_entry(engine, npc, arrival_turn=2)
                action = self._add_paid_action(entry, planned_turn=3, tier_key=tier_key)
                engine.state.today_arrival_plan_day = engine.state.day
                engine.state.today_arrival_plan = [entry]

                engine._process_entertainment({"events": []})

                self.assertEqual(action["status"], "completed")
                self.assertEqual(engine.state.today_income["entertainment"], expected_income)

    def test_paid_tier_satisfaction_gains_follow_tier_table(self):
        expectations = {
            "basic": 2,
            "standard": 4,
            "premium": 6,
        }

        for tier_key, expected_gain in expectations.items():
            with self.subTest(tier_key=tier_key):
                engine = self._new_engine()
                engine.state.day = 6
                engine.state.turn = 2
                npc = self._make_guest(110, total_satisfaction=40)
                engine.npc_pool.append(npc)
                entry = self._make_entry(engine, npc, arrival_turn=2)
                action = self._add_paid_action(entry, planned_turn=2, tier_key=tier_key)
                engine.state.today_arrival_plan_day = engine.state.day
                engine.state.today_arrival_plan = [entry]

                engine._process_entertainment({"events": []})

                self.assertEqual(action["satisfaction_gain"], expected_gain)
                self.assertEqual(npc.total_satisfaction, 40 + expected_gain)

    def test_free_entertainment_gives_zero_income_and_plus_one_satisfaction(self):
        engine = self._new_engine()
        engine.state.day = 7
        engine.state.turn = 4
        npc = self._make_guest(120, total_satisfaction=55)
        engine.npc_pool.append(npc)
        entry = self._make_entry(engine, npc, arrival_turn=3)
        action = self._add_free_action(entry, planned_turn=4)
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]
        result = {"events": []}

        engine._process_entertainment(result)

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["charged_amount"], 0)
        self.assertEqual(action["satisfaction_gain"], 1)
        self.assertEqual(engine.state.today_income["entertainment"], 0)
        self.assertEqual(npc.total_satisfaction, 56)
        self.assertEqual(npc.location, "entertainment")
        self.assertEqual(len(result["events"]), 1)

    def test_paid_entertainment_uses_saved_tier_key_without_redraw_or_downgrade(self):
        engine = self._new_engine()
        engine.state.day = 8
        engine.state.turn = 2
        engine.facilities["entertainment"].level = 0
        npc = self._make_guest(130, economic_level=0, total_satisfaction=60)
        engine.npc_pool.append(npc)
        entry = self._make_entry(engine, npc, arrival_turn=2)
        action = self._add_paid_action(entry, planned_turn=2, tier_key="premium")
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        with mock.patch(
            "game_engine.random.random",
            side_effect=AssertionError("turn execution should not re-roll entertainment"),
        ):
            engine._process_entertainment({"events": []})

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["tier_key"], "premium")
        self.assertEqual(engine.state.today_income["entertainment"], 65)
        self.assertEqual(npc.total_satisfaction, 66)

    def test_free_and_paid_entertainment_can_both_execute_on_different_turns(self):
        engine = self._new_engine()
        engine.state.day = 9
        npc = self._make_guest(140, total_satisfaction=70)
        engine.npc_pool.append(npc)
        entry = self._make_entry(engine, npc, arrival_turn=2)
        free_action = self._add_free_action(entry, planned_turn=2)
        paid_action = self._add_paid_action(entry, planned_turn=4, tier_key="standard")
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        engine.state.turn = 2
        engine._process_entertainment({"events": []})
        engine.state.turn = 4
        engine._process_entertainment({"events": []})

        self.assertEqual(free_action["status"], "completed")
        self.assertEqual(paid_action["status"], "completed")
        self.assertEqual(engine.state.today_income["entertainment"], 45)
        self.assertEqual(npc.total_satisfaction, 75)

    def test_completed_entertainment_action_does_not_repeat(self):
        engine = self._new_engine()
        engine.state.day = 10
        engine.state.turn = 3
        npc = self._make_guest(150, total_satisfaction=65)
        engine.npc_pool.append(npc)
        entry = self._make_entry(engine, npc, arrival_turn=2)
        action = self._add_paid_action(entry, planned_turn=3, tier_key="basic")
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        first_result = {"events": []}
        engine._process_entertainment(first_result)
        first_income = engine.state.today_income["entertainment"]
        first_satisfaction = npc.total_satisfaction

        second_result = {"events": []}
        engine._process_entertainment(second_result)

        self.assertEqual(action["status"], "completed")
        self.assertEqual(engine.state.today_income["entertainment"], first_income)
        self.assertEqual(npc.total_satisfaction, first_satisfaction)
        self.assertEqual(second_result["events"], [])

    def test_invalid_tier_key_is_skipped_without_silent_downgrade(self):
        engine = self._new_engine()
        engine.state.day = 11
        engine.state.turn = 2
        engine.facilities["entertainment"].level = 2
        npc = self._make_guest(160, total_satisfaction=50)
        engine.npc_pool.append(npc)
        entry = self._make_entry(engine, npc, arrival_turn=2)
        action = self._add_paid_action(entry, planned_turn=2, tier_key="vip")
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        engine._process_entertainment({"events": []})

        self.assertEqual(action["status"], "skipped")
        self.assertEqual(action["result"], "invalid_tier")
        self.assertEqual(engine.state.today_income["entertainment"], 0)
        self.assertEqual(npc.total_satisfaction, 50)


if __name__ == "__main__":
    unittest.main()
