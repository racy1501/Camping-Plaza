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
    def test_first_daily_demand_profile_call_generates_day_and_overnight_demand(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", return_value=0.99):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7) as day_mock:
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4) as overnight_mock:
                    profile = engine._ensure_daily_demand_profile()

        self.assertEqual(
            profile,
            {
                "natural_day_group_demand": 7,
                "natural_overnight_group_demand": 4,
                "reservation_request_available": False,
                "reservation_visit_type": None,
                "reservation_group_size": None,
                "reservation_processed": False,
                "reservation_result": None,
            },
        )
        self.assertEqual(engine.state.daily_demand_profile, profile)
        self.assertEqual(engine.state.daily_demand_profile_day, engine.state.day)
        day_mock.assert_called_once_with()
        overnight_mock.assert_called_once_with()

    def test_same_day_daily_demand_profile_does_not_recalculate(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", return_value=0.99):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7) as day_mock:
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4) as overnight_mock:
                    first_profile = engine._ensure_daily_demand_profile()
                    second_profile = engine._ensure_daily_demand_profile()

        self.assertIs(first_profile, second_profile)
        day_mock.assert_called_once_with()
        overnight_mock.assert_called_once_with()

    def test_same_day_daily_demand_profile_returns_same_result(self):
        engine = make_engine()
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 5,
            "natural_overnight_group_demand": 3,
            "reservation_request_available": False,
            "reservation_visit_type": None,
            "reservation_group_size": None,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_calculate_day_guest_demand", side_effect=AssertionError("day demand recalculated")):
            with mock.patch.object(
                engine,
                "_calculate_overnight_guest_demand",
                side_effect=AssertionError("overnight demand recalculated"),
            ):
                profile = engine._ensure_daily_demand_profile()

        self.assertIs(profile, engine.state.daily_demand_profile)
        self.assertEqual(
            profile,
            {
                "natural_day_group_demand": 5,
                "natural_overnight_group_demand": 3,
                "reservation_request_available": False,
                "reservation_visit_type": None,
                "reservation_group_size": None,
                "reservation_processed": False,
                "reservation_result": None,
            },
        )

    def test_daily_demand_profile_rebuilds_when_day_changes(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", return_value=0.99):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                    first_profile = engine._ensure_daily_demand_profile()

        engine.state.day += 1

        with mock.patch("game_engine.random.random", return_value=0.99):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=8):
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=5):
                    second_profile = engine._ensure_daily_demand_profile()

        self.assertNotEqual(first_profile, second_profile)
        self.assertEqual(second_profile["natural_day_group_demand"], 8)
        self.assertEqual(second_profile["natural_overnight_group_demand"], 5)
        self.assertEqual(engine.state.daily_demand_profile_day, engine.state.day)

    def test_new_day_daily_demand_profile_recalculates_day_and_overnight_once_each(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", return_value=0.99):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                    engine._ensure_daily_demand_profile()

        engine.state.day += 1

        with mock.patch("game_engine.random.random", return_value=0.99):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=8) as day_mock:
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=5) as overnight_mock:
                    engine._ensure_daily_demand_profile()

        day_mock.assert_called_once_with()
        overnight_mock.assert_called_once_with()

    def test_daily_demand_profile_sets_reservation_fields_when_not_triggered(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", return_value=0.30):
            with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                    profile = engine._ensure_daily_demand_profile()

        self.assertFalse(profile["reservation_request_available"])
        self.assertIsNone(profile["reservation_visit_type"])
        self.assertIsNone(profile["reservation_group_size"])

    def test_daily_demand_profile_can_generate_day_reservation(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.49]):
            with mock.patch("game_engine.random.randint", return_value=6):
                with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                    with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                        profile = engine._ensure_daily_demand_profile()

        self.assertTrue(profile["reservation_request_available"])
        self.assertEqual(profile["reservation_visit_type"], "day")
        self.assertEqual(profile["reservation_group_size"], 6)

    def test_daily_demand_profile_can_generate_overnight_reservation(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.50]):
            with mock.patch("game_engine.random.randint", return_value=5):
                with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                    with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                        profile = engine._ensure_daily_demand_profile()

        self.assertTrue(profile["reservation_request_available"])
        self.assertEqual(profile["reservation_visit_type"], "overnight")
        self.assertEqual(profile["reservation_group_size"], 5)

    def test_daily_demand_profile_reservation_group_size_uses_one_to_six(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.49]):
            with mock.patch("game_engine.random.randint", return_value=6) as randint_mock:
                with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                    with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                        profile = engine._ensure_daily_demand_profile()

        self.assertEqual(profile["reservation_group_size"], 6)
        randint_mock.assert_called_once_with(1, 6)

    def test_same_day_daily_demand_profile_does_not_reroll_reservation(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.49]) as random_mock:
            with mock.patch("game_engine.random.randint", return_value=6) as randint_mock:
                with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                    with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                        first_profile = engine._ensure_daily_demand_profile()
                        second_profile = engine._ensure_daily_demand_profile()

        self.assertIs(first_profile, second_profile)
        self.assertEqual(random_mock.call_count, 2)
        randint_mock.assert_called_once_with(1, 6)

    def test_new_day_daily_demand_profile_rerolls_reservation(self):
        engine = make_engine()
        engine.state.daily_demand_profile = None
        engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.49]):
            with mock.patch("game_engine.random.randint", return_value=6):
                with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=7):
                    with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=4):
                        first_profile = engine._ensure_daily_demand_profile()

        engine.state.day += 1

        with mock.patch("game_engine.random.random", side_effect=[0.30]) as random_mock:
            with mock.patch("game_engine.random.randint") as randint_mock:
                with mock.patch.object(engine, "_calculate_day_guest_demand", return_value=8):
                    with mock.patch.object(engine, "_calculate_overnight_guest_demand", return_value=5):
                        second_profile = engine._ensure_daily_demand_profile()

        self.assertNotEqual(first_profile, second_profile)
        self.assertFalse(second_profile["reservation_request_available"])
        self.assertIsNone(second_profile["reservation_visit_type"])
        self.assertIsNone(second_profile["reservation_group_size"])
        self.assertEqual(random_mock.call_count, 1)
        randint_mock.assert_not_called()

    def test_reservation_roll_does_not_depend_on_management_quality_or_development_degree(self):
        engine_a = make_engine()
        engine_b = make_engine()
        for engine in (engine_a, engine_b):
            engine.state.daily_demand_profile = None
            engine.state.daily_demand_profile_day = 0

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.49]):
            with mock.patch("game_engine.random.randint", return_value=6):
                with mock.patch.object(engine_a, "_calculate_day_guest_demand", return_value=7):
                    with mock.patch.object(engine_a, "_calculate_overnight_guest_demand", return_value=4):
                        with mock.patch.object(engine_a, "_calculate_management_quality", return_value=0.1):
                            with mock.patch.object(engine_a, "_calculate_development_degree", return_value=0.1):
                                profile_a = engine_a._ensure_daily_demand_profile()

        with mock.patch("game_engine.random.random", side_effect=[0.29, 0.49]):
            with mock.patch("game_engine.random.randint", return_value=6):
                with mock.patch.object(engine_b, "_calculate_day_guest_demand", return_value=8):
                    with mock.patch.object(engine_b, "_calculate_overnight_guest_demand", return_value=5):
                        with mock.patch.object(engine_b, "_calculate_management_quality", return_value=0.9):
                            with mock.patch.object(engine_b, "_calculate_development_degree", return_value=0.9):
                                profile_b = engine_b._ensure_daily_demand_profile()

        self.assertTrue(profile_a["reservation_request_available"])
        self.assertTrue(profile_b["reservation_request_available"])
        self.assertEqual(profile_a["reservation_visit_type"], "day")
        self.assertEqual(profile_b["reservation_visit_type"], "day")
        self.assertEqual(profile_a["reservation_group_size"], 6)
        self.assertEqual(profile_b["reservation_group_size"], 6)

    def test_day_reservation_auto_creates_accepted_record_and_charges_campsite_fee(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "day",
            "reservation_group_size": 4,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        def assign_hidden_tags(npc):
            npc.economic_level = 2
            npc.spending_habit = 1
            npc.temperament = 0

        with mock.patch.object(engine, "_assign_hidden_tags", side_effect=assign_hidden_tags):
            engine._generate_daily_reservation()

        self.assertIsNotNone(engine.state.reservation)
        self.assertEqual(engine.state.reservation["visit_type"], "day")
        self.assertEqual(engine.state.reservation["group_size"], 4)
        self.assertEqual(engine.state.reservation["arrival_day"], engine.state.day + 1)
        self.assertTrue(engine.state.reservation["paid"])
        self.assertEqual(engine.state.reservation["status"], "accepted")
        self.assertIn("npc_id", engine.state.reservation)
        self.assertEqual(engine.state.reservation["economic_level"], 2)
        self.assertEqual(engine.state.reservation["spending_habit"], 1)
        self.assertEqual(engine.state.reservation["temperament"], 0)
        self.assertEqual(engine.state.balance, 1000 + engine.CAMPSITE_FEE)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)
        self.assertIn(
            "接到一组4人的日间营位预约，客人将在明天到达。",
            engine.state.today_events,
        )
        self.assertTrue(engine.state.daily_demand_profile["reservation_processed"])
        self.assertEqual(engine.state.daily_demand_profile["reservation_result"], "accepted_day")

    def test_day_reservation_same_day_repeat_processing_does_not_duplicate_charge(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "day",
            "reservation_group_size": 3,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()
            first_reservation = dict(engine.state.reservation)
            engine._generate_daily_reservation()

        self.assertEqual(engine.state.balance, 1000 + engine.CAMPSITE_FEE)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)
        self.assertEqual(len(engine.state.today_events), 1)
        self.assertEqual(engine.state.reservation, first_reservation)

    def test_day_reservation_moves_to_next_day_arrival_plan_before_natural_guests(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.reservation = {
            "npc_id": 99,
            "group_size": 4,
            "visit_type": "day",
            "arrival_day": 2,
            "paid": True,
            "status": "accepted",
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 0,
            "total_satisfaction": 60,
        }

        natural_guest = engine._create_day_guest()
        engine.state.today_arrival_plan_day = 0

        with mock.patch.object(engine, "_calculate_daily_visitor_demand", return_value={"day_guest_count": 1, "overnight_guest_count": 0}):
            with mock.patch.object(engine, "_create_day_guest", return_value=natural_guest):
                with mock.patch.object(engine, "_roll_arrival_turn", return_value=2):
                    engine._ensure_today_arrival_plan()

        self.assertEqual(engine.state.today_arrival_plan[0]["source"], "reservation")
        self.assertEqual(engine.state.today_arrival_plan[0]["visit_type"], "day")
        self.assertTrue(engine.state.today_arrival_plan[0]["paid"])
        self.assertEqual(engine.state.today_arrival_plan[1]["source"], "natural_day")
        self.assertIsNone(engine.state.reservation)

    def test_day_reservation_arrival_does_not_charge_campsite_fee_twice(self):
        engine = make_engine()
        engine.state.turn = 2
        engine.state.balance = 1500
        engine.state.today_income["campsite"] = engine.CAMPSITE_FEE
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [
            {
                "npc_id": 11,
                "group_size": 3,
                "visit_type": "day",
                "economic_level": 1,
                "spending_habit": 1,
                "temperament": 1,
                "total_satisfaction": 60,
                "arrival_turn": 2,
                "planned_day": engine.state.day,
                "source": "reservation",
                "arrival_status": "pending",
                "planned_actions": [],
                "is_reserved": True,
                "paid": True,
                "tent_id": None,
            }
        ]
        result = {"events": []}

        engine._process_planned_arrivals(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 1)
        self.assertEqual(engine.state.balance, 1500)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)
        self.assertEqual(engine.state.today_arrival_plan[0]["arrival_status"], "arrived")
        self.assertEqual(engine.npc_pool[0].location, "campsite")
        self.assertTrue(engine.npc_pool[0].paid)

    def test_reservation_not_triggered_creates_no_charge_or_record(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": False,
            "reservation_visit_type": None,
            "reservation_group_size": None,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        engine._generate_daily_reservation()

        self.assertIsNone(engine.state.reservation)
        self.assertEqual(engine.state.balance, 1000)
        self.assertEqual(engine.state.today_income["campsite"], 0)
        self.assertEqual(engine.state.today_events, [])
        self.assertTrue(engine.state.daily_demand_profile["reservation_processed"])
        self.assertEqual(engine.state.daily_demand_profile["reservation_result"], "not_triggered")

    def test_overnight_reservation_profile_is_not_processed_as_day_reservation(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "overnight",
            "reservation_group_size": 5,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        engine._generate_daily_reservation()

        self.assertIsNone(engine.state.reservation)
        self.assertEqual(engine.state.balance, 1000)
        self.assertEqual(engine.state.today_income["campsite"], 0)
        self.assertEqual(engine.state.today_events, [])
        self.assertTrue(engine.state.daily_demand_profile["reservation_processed"])
        self.assertEqual(engine.state.daily_demand_profile["reservation_result"], "ignored_overnight")

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
