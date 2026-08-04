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
    td = tempfile.TemporaryDirectory(
        dir=os.path.join(
            os.environ.get("TEMP")
            or os.environ.get("TMP")
            or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        ),
        ignore_cleanup_errors=True,
    )
    _TEMP_DIRS.append(td)
    return CampingPlazaEngine(db_path=os.path.join(td.name, "test.db"))


def tearDownModule():
    for td in _TEMP_DIRS:
        try:
            td.cleanup()
        except PermissionError:
            pass
    _TEMP_DIRS.clear()


def snapshot_manual_reservation_state(engine: CampingPlazaEngine) -> dict:
    reservation = engine.state.reservation
    return {
        "balance": engine.state.balance,
        "today_income": dict(engine.state.today_income),
        "reservation": None if reservation is None else dict(reservation),
        "reserved_tent_id": engine.state.reserved_tent_id,
        "reserved_tent_day": engine.state.reserved_tent_day,
        "reputation_rate": engine.state.reputation_rate,
        "decisions_left": engine.state.decisions_left,
        "today_events": list(engine.state.today_events),
    }


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

    def test_pending_day_reservation_protects_one_slot_from_natural_guests(self):
        engine = make_engine()
        engine.state.today_arrival_plan = [
            {"npc_id": 11, "planned_day": engine.state.day,
             "source": "reservation", "visit_type": "day",
             "arrival_status": "pending", "paid": True,
             "total_satisfaction": 60, "economic_level": 1,
             "spending_habit": 1, "temperament": 1}
        ]
        for npc_id in range(100, 110):
            engine.state.today_arrival_plan.append({
                "npc_id": npc_id, "group_size": 1, "visit_type": "day",
                "arrival_turn": engine.state.turn, "planned_day": engine.state.day,
                "source": "natural_day", "arrival_status": "pending",
                "planned_actions": [], "is_reserved": False, "paid": False,
                "total_satisfaction": 60, "economic_level": 1,
                "spending_habit": 1, "temperament": 1,
            })

        engine._process_planned_arrivals({"events": []})

        natural_entries = [
            entry for entry in engine.state.today_arrival_plan
            if entry.get("source") == "natural_day"
        ]
        self.assertEqual(
            sum(entry["arrival_status"] == "arrived" for entry in natural_entries),
            9,
        )
        self.assertEqual(engine.state.day_campsite_groups_served, 9)

    def test_natural_guests_first_do_not_block_day_reservation(self):
        engine = make_engine()
        engine.state.today_arrival_plan = []
        for npc_id in range(100, 110):
            engine.state.today_arrival_plan.append({
                "npc_id": npc_id, "group_size": 1, "visit_type": "day",
                "arrival_turn": engine.state.turn, "planned_day": engine.state.day,
                "source": "natural_day", "arrival_status": "pending",
                "planned_actions": [], "is_reserved": False, "paid": False,
                "total_satisfaction": 60, "economic_level": 1,
                "spending_habit": 1, "temperament": 1,
            })
        engine.state.today_arrival_plan.append({
            "npc_id": 11, "group_size": 2, "visit_type": "day",
            "arrival_turn": engine.state.turn, "planned_day": engine.state.day,
            "source": "reservation", "arrival_status": "pending",
            "planned_actions": [], "is_reserved": True, "paid": True,
            "total_satisfaction": 60, "economic_level": 1,
            "spending_habit": 1, "temperament": 1,
        })

        engine._process_planned_arrivals({"events": []})

        reservation_entry = engine.state.today_arrival_plan[-1]
        self.assertEqual(reservation_entry["arrival_status"], "arrived")
        self.assertEqual(engine.state.day_campsite_groups_served, 10)

    def test_day_reservation_arrival_is_idempotent(self):
        engine = make_engine()
        engine.state.today_arrival_plan = [{
            "npc_id": 12, "group_size": 2, "visit_type": "day",
            "arrival_turn": engine.state.turn, "planned_day": engine.state.day,
            "source": "reservation", "arrival_status": "pending",
            "planned_actions": [], "is_reserved": True, "paid": True,
            "total_satisfaction": 60, "economic_level": 1,
            "spending_habit": 1, "temperament": 1,
        }]
        result = {"events": []}

        engine._process_planned_arrivals(result)
        engine._process_planned_arrivals(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 1)
        self.assertEqual(len([npc for npc in engine.npc_pool if npc.id == 12]), 1)
        self.assertEqual(engine.state.balance, 1000)
        self.assertEqual(engine.state.today_income["campsite"], 0)

    def test_natural_day_capacity_is_ten_without_reservation(self):
        engine = make_engine()
        self.assertEqual(engine._get_pending_day_reservation_count(), 0)
        self.assertEqual(engine.get_day_campsite_remaining(), 10)

    def test_restored_pending_day_reservation_keeps_slot_and_does_not_recharge(self):
        engine = make_engine()
        engine.state.balance = 1070
        engine.state.today_income["campsite"] = engine.CAMPSITE_FEE
        engine.state.today_arrival_plan = []
        engine.state.today_arrival_plan_day = 0
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 0,
            "natural_overnight_group_demand": 0,
            "reservation_request_available": False,
            "reservation_visit_type": None,
            "reservation_group_size": None,
            "reservation_processed": True,
            "reservation_result": "accepted_day",
        }
        engine.state.daily_demand_profile_day = engine.state.day
        engine.state.reservation = {
            "npc_id": 13, "group_size": 2, "visit_type": "day",
            "arrival_day": engine.state.day, "status": "accepted", "paid": True,
            "total_satisfaction": 60, "economic_level": 1,
            "spending_habit": 1, "temperament": 1,
        }
        engine.save_state()

        restored = CampingPlazaEngine(db_path=engine.db_path)
        restored.state.turn = 4
        if not restored.state.today_arrival_plan:
            restored.state.today_arrival_plan_day = 0
            restored._ensure_today_arrival_plan()
        restored.state.today_arrival_plan = [{
            "npc_id": 13, "group_size": 2, "visit_type": "day",
            "arrival_turn": restored.state.turn, "planned_day": restored.state.day,
            "source": "reservation", "arrival_status": "pending",
            "planned_actions": [], "is_reserved": True, "paid": True,
            "total_satisfaction": 60, "economic_level": 1,
            "spending_habit": 1, "temperament": 1,
        }]
        restored.state.balance = 1070
        restored.state.today_income["campsite"] = restored.CAMPSITE_FEE
        balance_before = restored.state.balance
        campsite_income_before = restored.state.today_income["campsite"]
        result = {"events": []}
        restored._process_planned_arrivals(result)
        restored._process_planned_arrivals(result)

        self.assertEqual(restored.state.day_campsite_groups_served, 1)
        self.assertEqual(restored.state.balance, balance_before)
        self.assertEqual(restored.state.today_income["campsite"], campsite_income_before)
        self.assertEqual(len([npc for npc in restored.npc_pool if npc.id == 13]), 1)

    def test_overnight_reservation_moves_to_arrival_plan_on_arrival_day(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.reservation = {
            "npc_id": 88,
            "group_size": 4,
            "visit_type": "overnight",
            "arrival_day": 2,
            "tent_id": 4,
            "paid": True,
            "status": "accepted",
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 0,
            "total_satisfaction": 60,
        }
        engine.state.reserved_tent_id = 4
        engine.state.reserved_tent_day = 2
        natural_day_guest = engine._create_day_guest()
        natural_overnight_guest = engine._create_overnight_guest()
        engine.state.today_arrival_plan_day = 0

        with mock.patch.object(
            engine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 1},
        ):
            with mock.patch.object(engine, "_create_day_guest", return_value=natural_day_guest):
                with mock.patch.object(engine, "_create_overnight_guest", return_value=natural_overnight_guest):
                    with mock.patch.object(engine, "_roll_arrival_turn", side_effect=[2, 3, 4]):
                        engine._ensure_today_arrival_plan()

        reservation_entry = engine.state.today_arrival_plan[0]
        self.assertEqual(reservation_entry["source"], "reservation")
        self.assertEqual(reservation_entry["visit_type"], "overnight")
        self.assertTrue(reservation_entry["paid"])
        self.assertEqual(reservation_entry["tent_id"], 4)
        self.assertEqual(reservation_entry["arrival_turn"], 2)
        self.assertEqual(reservation_entry["npc_id"], 88)
        self.assertEqual(reservation_entry["economic_level"], 1)
        self.assertEqual(reservation_entry["spending_habit"], 2)
        self.assertEqual(reservation_entry["temperament"], 0)
        self.assertEqual(engine.state.today_arrival_plan[1]["source"], "natural_day")
        self.assertEqual(engine.state.today_arrival_plan[2]["source"], "natural_overnight")
        self.assertIsNone(engine.state.reservation)
        self.assertEqual(engine.state.reserved_tent_id, 4)
        self.assertEqual(engine.state.reserved_tent_day, 2)

    def test_same_day_ensure_today_arrival_plan_does_not_duplicate_overnight_reservation_entry(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.reservation = {
            "npc_id": 77,
            "group_size": 3,
            "visit_type": "overnight",
            "arrival_day": 2,
            "tent_id": 3,
            "paid": True,
            "status": "accepted",
            "economic_level": 0,
            "spending_habit": 1,
            "temperament": 2,
            "total_satisfaction": 60,
        }
        engine.state.reserved_tent_id = 3
        engine.state.reserved_tent_day = 2
        engine.state.today_arrival_plan_day = 0

        with mock.patch.object(
            engine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ):
            with mock.patch.object(engine, "_roll_arrival_turn", return_value=2) as roll_mock:
                first_result = engine._ensure_today_arrival_plan()
                second_result = engine._ensure_today_arrival_plan()

        self.assertTrue(first_result)
        self.assertFalse(second_result)
        self.assertEqual(len(engine.state.today_arrival_plan), 1)
        self.assertEqual(engine.state.today_arrival_plan[0]["npc_id"], 77)
        self.assertEqual(roll_mock.call_count, 1)

    def test_process_reservations_does_not_create_second_overnight_guest_for_existing_plan_entry(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.reservation = {
            "npc_id": 66,
            "group_size": 2,
            "visit_type": "overnight",
            "arrival_day": 2,
            "tent_id": 2,
            "paid": True,
            "status": "accepted",
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "total_satisfaction": 60,
        }
        engine.state.reserved_tent_id = 2
        engine.state.reserved_tent_day = 2
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "reserved"
        engine.state.today_arrival_plan_day = 0
        result = {"events": []}

        with mock.patch.object(
            engine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ):
            with mock.patch.object(engine, "_roll_arrival_turn", return_value=2):
                engine._ensure_today_arrival_plan()

        engine._process_reservations(result)
        engine._process_planned_arrivals(result)
        engine._process_reservations(result)

        self.assertEqual(len(engine.npc_pool), 1)
        self.assertEqual(engine.npc_pool[0].id, 66)
        self.assertEqual(engine.state.today_arrival_plan[0]["arrival_status"], "arrived")

    def test_overnight_reservation_arrival_uses_locked_tent_without_recharging(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.balance = 1500
        engine.state.today_income["accommodation"] = engine.TENT_PRICES[4]
        engine.state.reserved_tent_id = 4
        engine.state.reserved_tent_day = 2
        engine.tents[4].is_unlocked = True
        engine.tents[4].status = "reserved"
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [
            {
                "npc_id": 101,
                "group_size": 4,
                "visit_type": "overnight",
                "economic_level": 1,
                "spending_habit": 2,
                "temperament": 0,
                "total_satisfaction": 60,
                "arrival_turn": 2,
                "planned_day": engine.state.day,
                "source": "reservation",
                "arrival_status": "pending",
                "planned_actions": [],
                "is_reserved": True,
                "paid": True,
                "tent_id": 4,
            }
        ]
        result = {"events": []}

        engine._process_planned_arrivals(result)

        self.assertEqual(engine.npc_pool[0].location, "tent_4")
        self.assertEqual(engine.npc_pool[0].visit_type, "overnight")
        self.assertEqual(engine.tents[4].status, "occupied")
        self.assertEqual(engine.tents[4].occupied_by, 101)
        self.assertEqual(engine.state.today_arrival_plan[0]["arrival_status"], "arrived")
        self.assertEqual(engine.state.balance, 1500)
        self.assertEqual(engine.state.today_income["accommodation"], engine.TENT_PRICES[4])
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertIsNone(engine.state.reserved_tent_day)

    def test_overnight_reservation_arrival_waits_when_locked_tent_is_temporarily_unavailable(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.balance = 1500
        engine.state.today_income["accommodation"] = engine.TENT_PRICES[2]
        engine.state.reserved_tent_id = 2
        engine.state.reserved_tent_day = 2
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "cleaning"
        engine.tents[2].occupied_by = 555
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [
            {
                "npc_id": 202,
                "group_size": 2,
                "visit_type": "overnight",
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
                "tent_id": 2,
            }
        ]
        result = {"events": []}

        engine._process_planned_arrivals(result)
        engine._process_planned_arrivals(result)

        self.assertEqual(engine.state.today_arrival_plan[0]["arrival_status"], "pending")
        self.assertEqual(engine.state.balance, 1500)
        self.assertEqual(engine.state.today_income["accommodation"], engine.TENT_PRICES[2])
        self.assertEqual(engine.state.reserved_tent_id, 2)
        self.assertEqual(engine.state.reserved_tent_day, 2)
        self.assertEqual(engine.tents[2].status, "cleaning")
        self.assertEqual(engine.tents[2].occupied_by, 555)
        self.assertEqual(len(engine.npc_pool), 0)

    def test_natural_overnight_arrival_logic_still_checks_in_normally(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.tents[1].is_unlocked = True
        engine.tents[1].status = "available"
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [
            {
                "npc_id": 303,
                "group_size": 2,
                "visit_type": "overnight",
                "economic_level": 1,
                "spending_habit": 1,
                "temperament": 1,
                "total_satisfaction": 60,
                "arrival_turn": 2,
                "planned_day": engine.state.day,
                "source": "natural_overnight",
                "arrival_status": "pending",
                "planned_actions": [],
                "is_reserved": False,
                "paid": False,
                "tent_id": None,
            }
        ]
        result = {"events": []}

        engine._process_planned_arrivals(result)

        self.assertEqual(engine.state.today_arrival_plan[0]["arrival_status"], "arrived")
        self.assertEqual(engine.npc_pool[0].location, "tent_1")
        self.assertEqual(engine.tents[1].status, "occupied")
        self.assertEqual(engine.tents[1].occupied_by, 303)
        self.assertEqual(engine.state.today_income["accommodation"], engine.TENT_PRICES[1])

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

    def test_overnight_reservation_auto_selects_smallest_suitable_tent(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.tents[1].is_unlocked = True
        engine.tents[2].is_unlocked = True
        engine.tents[3].is_unlocked = True
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "overnight",
            "reservation_group_size": 2,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()

        self.assertIsNotNone(engine.state.reservation)
        self.assertEqual(engine.state.reservation["visit_type"], "overnight")
        self.assertEqual(engine.state.reservation["tent_id"], 1)
        self.assertEqual(engine.state.reserved_tent_id, 1)
        self.assertEqual(engine.state.reserved_tent_day, engine.state.day + 1)
        self.assertEqual(engine.state.balance, 1000 + engine.TENT_PRICES[1])
        self.assertEqual(engine.state.today_income["accommodation"], engine.TENT_PRICES[1])
        self.assertIn("接到一组2人的过夜预约，已为明天预留1号帐篷。", engine.state.today_events)
        self.assertTrue(engine.state.daily_demand_profile["reservation_processed"])
        self.assertEqual(engine.state.daily_demand_profile["reservation_result"], "accepted_overnight")

    def test_overnight_reservation_selects_smallest_id_when_capacities_tie(self):
        engine = make_engine()
        engine.state.today_events = []
        for tent in engine.tents.values():
            tent.is_unlocked = False
        engine.tents[4].is_unlocked = True
        engine.tents[5].is_unlocked = True
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "overnight",
            "reservation_group_size": 4,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()

        self.assertEqual(engine.state.reservation["tent_id"], 4)
        self.assertEqual(engine.state.reserved_tent_id, 4)

    def test_overnight_reservation_ignores_current_tent_status_when_selecting(self):
        engine = make_engine()
        engine.state.today_events = []
        for tent in engine.tents.values():
            tent.is_unlocked = True
        engine.tents[1].status = "broken"
        engine.tents[2].status = "occupied"
        engine.tents[3].status = "cleaning"
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "overnight",
            "reservation_group_size": 2,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()

        self.assertEqual(engine.state.reservation["tent_id"], 1)

    def test_overnight_reservation_does_not_overwrite_current_tent_status_or_occupied_by(self):
        engine = make_engine()
        engine.state.today_events = []
        for tent in engine.tents.values():
            tent.is_unlocked = False
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "occupied"
        engine.tents[2].occupied_by = 777
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "overnight",
            "reservation_group_size": 2,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()

        self.assertEqual(engine.tents[2].status, "occupied")
        self.assertEqual(engine.tents[2].occupied_by, 777)
        self.assertEqual(engine.state.reserved_tent_id, 2)

    def test_overnight_reservation_same_day_repeat_processing_does_not_duplicate_charge_or_lock(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.tents[1].is_unlocked = True
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 2,
            "natural_overnight_group_demand": 1,
            "reservation_request_available": True,
            "reservation_visit_type": "overnight",
            "reservation_group_size": 2,
            "reservation_processed": False,
            "reservation_result": None,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()
            first_reservation = dict(engine.state.reservation)
            first_reserved_tent_id = engine.state.reserved_tent_id
            engine._generate_daily_reservation()

        self.assertEqual(engine.state.balance, 1000 + engine.TENT_PRICES[1])
        self.assertEqual(engine.state.today_income["accommodation"], engine.TENT_PRICES[1])
        self.assertEqual(len(engine.state.today_events), 1)
        self.assertEqual(engine.state.reservation, first_reservation)
        self.assertEqual(engine.state.reserved_tent_id, first_reserved_tent_id)

    def test_overnight_reservation_insufficient_capacity_does_not_charge_or_create_reservation(self):
        engine = make_engine()
        engine.state.today_events = []
        engine.state.reputation_rate = 75.0
        for tent in engine.tents.values():
            tent.is_unlocked = False
        engine.tents[1].is_unlocked = True
        engine.tents[2].is_unlocked = True
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

        with mock.patch.object(engine, "_record_reservation_rejection_event", side_effect=AssertionError("old rejection logic called")):
            engine._generate_daily_reservation()

        self.assertIsNone(engine.state.reservation)
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertIsNone(engine.state.reserved_tent_day)
        self.assertEqual(engine.state.balance, 1000)
        self.assertEqual(engine.state.today_income["accommodation"], 0)
        self.assertEqual(engine.state.reputation_rate, 75.0)
        self.assertTrue(engine.state.daily_demand_profile["reservation_processed"])
        self.assertEqual(engine.state.daily_demand_profile["reservation_result"], "rejected_overnight_capacity")

    def test_overnight_reservation_insufficient_capacity_event_reports_correct_max_capacity(self):
        engine = make_engine()
        engine.state.today_events = []
        for tent in engine.tents.values():
            tent.is_unlocked = False
        engine.tents[1].is_unlocked = True
        engine.tents[4].is_unlocked = True
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

        self.assertEqual(
            engine.state.today_events,
            ["接到一组5人的过夜预约，但当前已开放的帐篷最大只能容纳4人，本次未能接下。"],
        )

    def test_day_reservation_logic_still_charges_campsite_fee_normally(self):
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

        with mock.patch.object(engine, "_assign_hidden_tags"):
            engine._generate_daily_reservation()

        self.assertEqual(engine.state.reservation["visit_type"], "day")
        self.assertEqual(engine.CAMPSITE_FEE, 70)
        self.assertEqual(engine.state.balance, 1000 + engine.CAMPSITE_FEE)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertEqual(engine.state.daily_demand_profile["reservation_result"], "accepted_day")

    def test_accept_reservation_is_now_a_noop_compatibility_interface(self):
        engine = make_engine()
        engine.state.balance = 1350
        engine.state.today_income["accommodation"] = 180
        engine.state.today_income["campsite"] = 20
        engine.state.reputation_rate = 72.5
        engine.state.decisions_left = 2
        engine.state.today_events.append("existing event")
        engine.state.reservation = {
            "visit_type": "overnight",
            "group_size": 4,
            "arrival_day": engine.state.day + 1,
            "tent_id": 4,
            "paid": True,
            "status": "accepted",
            "npc_id": 101,
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 0,
        }
        engine.state.reserved_tent_id = 4
        engine.state.reserved_tent_day = engine.state.day + 1
        before = snapshot_manual_reservation_state(engine)

        result = engine.accept_reservation(group_size=6)

        self.assertEqual(result, {"success": True, "message": "预约已改为自动结算，无需手动处理"})
        self.assertEqual(snapshot_manual_reservation_state(engine), before)

    def test_reject_reservation_is_now_a_noop_compatibility_interface(self):
        engine = make_engine()
        engine.state.balance = 1350
        engine.state.today_income["accommodation"] = 180
        engine.state.today_income["campsite"] = 20
        engine.state.reputation_rate = 72.5
        engine.state.decisions_left = 2
        engine.state.today_events.append("existing event")
        engine.state.reservation = {
            "visit_type": "day",
            "group_size": 3,
            "arrival_day": engine.state.day + 1,
            "paid": True,
            "status": "accepted",
            "npc_id": 102,
            "economic_level": 2,
            "spending_habit": 1,
            "temperament": 0,
        }
        engine.state.reserved_tent_id = 4
        engine.state.reserved_tent_day = engine.state.day + 1
        before = snapshot_manual_reservation_state(engine)

        result = engine.reject_reservation()

        self.assertEqual(result, {"success": True, "message": "预约已改为自动结算，无需手动处理"})
        self.assertEqual(snapshot_manual_reservation_state(engine), before)

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
