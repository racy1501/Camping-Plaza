import os
import sys
import tempfile
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine


class HotSpringPlannedActionsTests(unittest.TestCase):
    def setUp(self):
        self.db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self.db_dir, exist_ok=True)
        self.db_path = os.path.join(self.db_dir, "hot_spring_planned_actions.sqlite")
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

    def _entry(self, arrival_turn=2, source="natural_day"):
        return {
            "arrival_turn": arrival_turn,
            "source": source,
            "planned_actions": [],
            "spending_habit": 1,
            "economic_level": 1,
        }

    def test_unbuilt_hot_spring_does_not_roll_or_generate(self):
        with mock.patch("game_engine.random.random", side_effect=AssertionError("must not roll")):
            self.assertIsNone(self.engine._build_hot_spring_planned_action())

    def test_built_hot_spring_hits_at_thirty_percent(self):
        self.engine.state.hot_spring_built = True
        with mock.patch("game_engine.random.random", return_value=0.29):
            action = self.engine._build_hot_spring_planned_action()
        self.assertEqual(action, {"action": "hot_spring", "status": "pending"})

    def test_built_hot_spring_misses_at_thirty_percent(self):
        self.engine.state.hot_spring_built = True
        with mock.patch("game_engine.random.random", return_value=0.30):
            self.assertIsNone(self.engine._build_hot_spring_planned_action())

    def test_four_independent_behaviors_share_one_optional_pool(self):
        self.engine.state.hot_spring_built = True
        actions = [
            {"action": "dining", "status": "pending"},
            {"action": "paid_entertainment", "status": "pending"},
            {"action": "free_entertainment", "status": "pending"},
            {"action": "hot_spring", "status": "pending"},
        ]
        with mock.patch.object(self.engine, "_build_dining_planned_action", return_value=actions[0]), \
             mock.patch.object(self.engine, "_build_paid_entertainment_planned_action", return_value=actions[1]), \
             mock.patch.object(self.engine, "_build_free_entertainment_planned_action", return_value=actions[2]), \
             mock.patch.object(self.engine, "_build_hot_spring_planned_action", return_value=actions[3]), \
             mock.patch("game_engine.random.shuffle"), \
             mock.patch("game_engine.random.sample", return_value=[2, 3, 4, 5]):
            entry = self._entry(arrival_turn=2)
            self.engine._append_planned_actions(entry)
        self.assertEqual(len(entry["planned_actions"]), 4)
        self.assertEqual(
            {action["action"] for action in entry["planned_actions"]},
            {"dining", "paid_entertainment", "free_entertainment", "hot_spring"},
        )

    def test_behavior_pool_is_truncated_without_hot_spring_priority(self):
        self.engine.state.hot_spring_built = True
        builders = [
            ("_build_dining_planned_action", {"action": "dining", "status": "pending"}),
            ("_build_paid_entertainment_planned_action", {"action": "paid_entertainment", "status": "pending"}),
            ("_build_free_entertainment_planned_action", {"action": "free_entertainment", "status": "pending"}),
            ("_build_hot_spring_planned_action", {"action": "hot_spring", "status": "pending"}),
        ]
        patches = [mock.patch.object(self.engine, name, return_value=action) for name, action in builders]
        with patches[0], patches[1], patches[2], patches[3], \
             mock.patch("game_engine.random.shuffle"), \
             mock.patch("game_engine.random.sample", return_value=[4, 5]):
            entry = self._entry(arrival_turn=4)
            self.engine._append_planned_actions(entry)
        self.assertEqual(len(entry["planned_actions"]), 2)
        self.assertEqual({action["planned_turn"] for action in entry["planned_actions"]}, {4, 5})
        self.assertNotIn("hot_spring", {action["action"] for action in entry["planned_actions"]})

    def test_turn_two_and_turn_four_use_their_existing_activity_windows(self):
        self.engine.state.hot_spring_built = True
        for arrival_turn, expected_turns in ((2, {2, 3, 4, 5}), (4, {4, 5})):
            with self.subTest(arrival_turn=arrival_turn):
                action = {"action": "hot_spring", "status": "pending"}
                entry = self._entry(arrival_turn=arrival_turn)
                with mock.patch.object(self.engine, "_build_dining_planned_action", return_value=None), \
                     mock.patch.object(self.engine, "_build_paid_entertainment_planned_action", return_value=None), \
                     mock.patch.object(self.engine, "_build_free_entertainment_planned_action", return_value=None), \
                     mock.patch.object(self.engine, "_build_hot_spring_planned_action", return_value=action), \
                     mock.patch("game_engine.random.sample", return_value=[arrival_turn]):
                    self.engine._append_planned_actions(entry)
                self.assertEqual(entry["planned_actions"][0]["planned_turn"], arrival_turn)
                self.assertIn(
                    entry["planned_actions"][0]["planned_turn"],
                    expected_turns,
                )

    def test_natural_and_reservation_sources_share_the_same_builder(self):
        self.engine.state.hot_spring_built = True
        with mock.patch.object(self.engine, "_build_dining_planned_action", return_value=None), \
             mock.patch.object(self.engine, "_build_paid_entertainment_planned_action", return_value=None), \
             mock.patch.object(self.engine, "_build_free_entertainment_planned_action", return_value=None), \
             mock.patch.object(self.engine, "_build_hot_spring_planned_action", return_value={"action": "hot_spring", "status": "pending"}), \
             mock.patch("game_engine.random.sample", return_value=[2]):
            natural = self._entry(source="natural_day")
            reservation = self._entry(source="reservation")
            self.engine._append_planned_actions(natural)
            self.engine._append_planned_actions(reservation)
        self.assertEqual(natural["planned_actions"], reservation["planned_actions"])

    def test_restored_built_state_allows_new_hot_spring_plan(self):
        self.engine.state.hot_spring_built = True
        self.assertTrue(self.engine.save_state())
        restored = CampingPlazaEngine(db_path=self.db_path)
        with mock.patch("game_engine.random.random", return_value=0.0):
            action = restored._build_hot_spring_planned_action()
        self.assertEqual(action["action"], "hot_spring")

    def test_hot_spring_action_has_no_execution_fields_or_other_action_changes(self):
        self.engine.state.hot_spring_built = True
        with mock.patch("game_engine.random.random", return_value=0.0):
            action = self.engine._build_hot_spring_planned_action()
        self.assertEqual(set(action), {"action", "status"})
        self.assertEqual(CampingPlazaEngine.TURN_PLAN_ACTIONS["clean_tents"]["kind"], "free")
        self.assertNotIn("hot_spring", CampingPlazaEngine.TURN_PLAN_ACTIONS)


if __name__ == "__main__":
    unittest.main()
