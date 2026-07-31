import os
import sys
import tempfile
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine

_TEMP_DIRS = []


def make_engine() -> CampingPlazaEngine:
    td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    _TEMP_DIRS.append(td)
    return CampingPlazaEngine(db_path=os.path.join(td.name, "test.db"))


def tearDownModule():
    for td in _TEMP_DIRS:
        try:
            td.cleanup()
        except PermissionError:
            pass
    _TEMP_DIRS.clear()


class DailyDemandHelperTests(unittest.TestCase):
    def test_day_guest_demand_uses_management_quality_development_degree_and_probabilistic_round(self):
        engine = make_engine()

        with mock.patch.object(engine, "_calculate_management_quality", return_value=0.8) as quality_mock:
            with mock.patch.object(engine, "_calculate_development_degree", return_value=0.9) as development_mock:
                with mock.patch.object(engine, "_probabilistic_round", return_value=7) as round_mock:
                    result = engine._calculate_day_guest_demand()

        self.assertEqual(result, 7)
        quality_mock.assert_called_once_with()
        development_mock.assert_called_once_with()
        round_mock.assert_called_once_with(mock.ANY)
        self.assertAlmostEqual(round_mock.call_args.args[0], 7.2)

    def test_day_guest_demand_ignores_current_remaining_capacity(self):
        engine_a = make_engine()
        engine_b = make_engine()
        engine_a.state.day_campsite_groups_served = 0
        engine_b.state.day_campsite_groups_served = 9

        for engine in (engine_a, engine_b):
            engine.state.reputation_rate = 80.0
            engine.facilities["dining"].level = 2
            engine.facilities["entertainment"].level = 2
            engine.facilities["greenery"].greenery_satisfaction = 10.0
            for tent in engine.tents.values():
                tent.is_unlocked = True

        with mock.patch("game_engine.random.random", return_value=0.0):
            demand_a = engine_a._calculate_day_guest_demand()
            demand_b = engine_b._calculate_day_guest_demand()

        self.assertEqual(engine_a.get_day_campsite_remaining(), 10)
        self.assertEqual(engine_b.get_day_campsite_remaining(), 1)
        self.assertEqual(demand_a, demand_b)

    def test_day_guest_demand_can_exceed_current_remaining_capacity(self):
        engine = make_engine()
        engine.state.day_campsite_groups_served = 9

        with mock.patch.object(engine, "_calculate_management_quality", return_value=0.8):
            with mock.patch.object(engine, "_calculate_development_degree", return_value=0.9):
                with mock.patch.object(engine, "_probabilistic_round", return_value=7):
                    result = engine._calculate_day_guest_demand()

        self.assertEqual(engine.get_day_campsite_remaining(), 1)
        self.assertEqual(result, 7)
        self.assertGreater(result, engine.get_day_campsite_remaining())

    def test_day_guest_demand_does_not_call_old_random_range_logic(self):
        engine = make_engine()

        with mock.patch("game_engine.random.randint", side_effect=AssertionError("old random range called")):
            with mock.patch.object(engine, "_calculate_management_quality", return_value=0.6):
                with mock.patch.object(engine, "_calculate_development_degree", return_value=0.5):
                    with mock.patch.object(engine, "_probabilistic_round", return_value=3):
                        result = engine._calculate_day_guest_demand()

        self.assertEqual(result, 3)

    def test_overnight_guest_demand_uses_management_quality_development_degree_and_probabilistic_round(self):
        engine = make_engine()

        with mock.patch.object(engine, "_calculate_management_quality", return_value=0.8) as quality_mock:
            with mock.patch.object(engine, "_calculate_development_degree", return_value=0.9) as development_mock:
                with mock.patch.object(engine, "_probabilistic_round", return_value=4) as round_mock:
                    result = engine._calculate_overnight_guest_demand()

        self.assertEqual(result, 4)
        quality_mock.assert_called_once_with()
        development_mock.assert_called_once_with()
        round_mock.assert_called_once_with(mock.ANY)
        self.assertAlmostEqual(round_mock.call_args.args[0], 4.32)

    def test_overnight_guest_demand_ignores_tent_status(self):
        engine_a = make_engine()
        engine_b = make_engine()
        status_map = {
            1: "available",
            2: "occupied",
            3: "cleaning",
            4: "broken",
            5: "reserved",
            6: "occupied",
        }

        for engine in (engine_a, engine_b):
            engine.state.reputation_rate = 80.0
            engine.facilities["dining"].level = 2
            engine.facilities["entertainment"].level = 2
            engine.facilities["greenery"].greenery_satisfaction = 10.0
            for tent in engine.tents.values():
                tent.is_unlocked = True

        for tent_id, tent in engine_a.tents.items():
            tent.status = status_map[tent_id]
        for tent in engine_b.tents.values():
            tent.status = "available"

        with mock.patch("game_engine.random.random", return_value=0.0):
            demand_a = engine_a._calculate_overnight_guest_demand()
            demand_b = engine_b._calculate_overnight_guest_demand()

        self.assertEqual(demand_a, demand_b)

    def test_overnight_guest_demand_can_exceed_current_receivable_tent_count(self):
        engine = make_engine()
        for tent in engine.tents.values():
            tent.is_unlocked = True
            tent.status = "occupied"
        engine.tents[1].status = "available"

        with mock.patch.object(engine, "_calculate_management_quality", return_value=0.8):
            with mock.patch.object(engine, "_calculate_development_degree", return_value=0.9):
                with mock.patch.object(engine, "_probabilistic_round", return_value=4):
                    result = engine._calculate_overnight_guest_demand()

        receivable_tent_count = sum(
            1 for tent in engine.tents.values() if tent.is_unlocked and tent.status == "available"
        )
        self.assertEqual(receivable_tent_count, 1)
        self.assertEqual(result, 4)
        self.assertGreater(result, receivable_tent_count)

    def test_overnight_guest_demand_does_not_call_old_per_tent_random_logic(self):
        engine = make_engine()

        with mock.patch("game_engine.random.random", side_effect=AssertionError("old per-tent random called")):
            with mock.patch.object(engine, "_get_unlocked_tents", side_effect=AssertionError("old unlocked tent scan called")):
                with mock.patch.object(engine, "_calculate_management_quality", return_value=0.6):
                    with mock.patch.object(engine, "_calculate_development_degree", return_value=0.5):
                        with mock.patch.object(engine, "_probabilistic_round", return_value=2):
                            result = engine._calculate_overnight_guest_demand()

        self.assertEqual(result, 2)

    def test_management_quality_uses_equal_weighted_four_terms(self):
        engine = make_engine()
        engine.state.reputation_rate = 80.0
        engine.facilities["dining"].level = 2
        engine.facilities["entertainment"].level = 1
        engine.facilities["greenery"].greenery_satisfaction = 5.0

        expected = (0.8 + 1.0 + (2 / 3) + 0.5) / 4

        self.assertAlmostEqual(engine._calculate_management_quality(), expected)

    def test_management_quality_does_not_read_total_reviews(self):
        engine = make_engine()
        engine.state.reputation_rate = 65.0
        engine.state.total_reviews = 0
        baseline = engine._calculate_management_quality()

        engine.state.total_reviews = 99999

        self.assertAlmostEqual(engine._calculate_management_quality(), baseline)

    def test_development_degree_changes_with_unlocked_tent_count(self):
        engine = make_engine()

        for unlocked_count in (1, 3, 6):
            for tent in engine.tents.values():
                tent.is_unlocked = tent.id <= unlocked_count
            expected = (1 + unlocked_count / 6) / 2
            with self.subTest(unlocked_count=unlocked_count):
                self.assertAlmostEqual(engine._calculate_development_degree(), expected)

    def test_development_degree_ignores_tent_status(self):
        engine = make_engine()
        status_map = {
            1: "occupied",
            2: "cleaning",
            3: "broken",
            4: "reserved",
            5: "available",
            6: "occupied",
        }

        for tent_id, tent in engine.tents.items():
            tent.is_unlocked = tent_id in (1, 2, 3, 4)
            tent.status = status_map[tent_id]

        self.assertAlmostEqual(engine._calculate_development_degree(), (1 + 4 / 6) / 2)

    def test_probabilistic_round_returns_integer_without_random(self):
        engine = make_engine()

        with mock.patch("game_engine.random.random") as random_mock:
            result = engine._probabilistic_round(3.0)

        self.assertEqual(result, 3)
        random_mock.assert_not_called()

    def test_probabilistic_round_rounds_up_when_roll_is_below_fraction(self):
        engine = make_engine()

        with mock.patch("game_engine.random.random", return_value=0.69):
            result = engine._probabilistic_round(3.7)

        self.assertEqual(result, 4)

    def test_probabilistic_round_rounds_down_when_roll_is_not_below_fraction(self):
        engine = make_engine()

        with mock.patch("game_engine.random.random", return_value=0.70):
            result = engine._probabilistic_round(3.7)

        self.assertEqual(result, 3)
