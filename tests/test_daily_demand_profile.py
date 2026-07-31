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
