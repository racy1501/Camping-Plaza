import os
import sys
import uuid
import unittest
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


def _temp_db_path() -> str:
    return os.path.join(_PROJECT_ROOT, f".arrival_plan_phase1_{uuid.uuid4().hex}.sqlite")


def _cleanup_db_path(db_path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = db_path + suffix if suffix else db_path
        if os.path.exists(path):
            os.remove(path)


class ArrivalPlanPhase1Tests(unittest.TestCase):
    def _new_engine(self):
        db_path = _temp_db_path()
        self.addCleanup(_cleanup_db_path, db_path)
        engine = CampingPlazaEngine(db_path=db_path)
        engine.state.today_arrival_plan_day = 0
        engine.state.today_arrival_plan = []
        return engine

    def _make_guest(self, npc_id: int, visit_type: str, satisfaction: int = 70):
        npc = NPCGroup(
            id=npc_id,
            group_size=2,
            visit_type=visit_type,
            total_satisfaction=satisfaction,
        )
        npc.economic_level = 1
        npc.spending_habit = 2
        npc.temperament = 0
        return npc

    def _auto_generate_overnight_reservation(self, engine, group_size: int = 2) -> dict:
        engine.state.daily_demand_profile = {
            "natural_day_group_demand": 0,
            "natural_overnight_group_demand": 0,
            "reservations_processed": False,
        }
        engine.state.daily_demand_profile_day = engine.state.day

        def assign_hidden_tags(npc):
            npc.economic_level = 1
            npc.spending_habit = 2
            npc.temperament = 0

        with mock.patch.object(engine, "_assign_hidden_tags", side_effect=assign_hidden_tags), \
             mock.patch("game_engine.random.random", side_effect=[0.99] * 10 + [0.1]), \
             mock.patch("game_engine.random.randint", return_value=group_size):
            engine._generate_daily_reservation()

        self.assertEqual(len(engine.state.reservations), 1)
        reservation = engine.state.reservations[0]
        self.assertEqual(reservation["visit_type"], "overnight")
        self.assertEqual(reservation["group_size"], group_size)
        self.assertEqual(reservation["arrival_day"], engine.state.day + 1)
        self.assertTrue(reservation["paid"])
        self.assertEqual(reservation["status"], "accepted")
        self.assertTrue(engine.state.daily_demand_profile["reservations_processed"])
        return dict(reservation)

    def _ensure_overnight_arrival_plan(self, engine, arrival_turn: int, day_guest_count: int = 0):
        natural_guest = self._make_guest(901, "day")
        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": day_guest_count, "overnight_guest_count": 0},
        ), mock.patch.object(
            CampingPlazaEngine,
            "_roll_arrival_turn",
            side_effect=[arrival_turn] + [arrival_turn + 1] * day_guest_count,
        ) as arrival_turn_mock, mock.patch.object(
            engine, "_create_day_guest", return_value=natural_guest
        ), mock.patch.object(
            engine, "_append_planned_actions", wraps=engine._append_planned_actions
        ) as append_actions_mock, mock.patch(
            "game_engine.random.random", return_value=0.99
        ):
            self.assertTrue(engine._ensure_today_arrival_plan())
        return arrival_turn_mock, append_actions_mock

    def test_daily_demand_is_calculated_once_and_plan_is_kept(self):
        engine = self._new_engine()
        engine.state.day = 5
        engine.state.turn = 1

        demand = {"day_guest_count": 12, "overnight_guest_count": 0}
        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value=demand,
        ) as demand_mock, mock.patch.object(
            CampingPlazaEngine,
            "_roll_arrival_turn",
            side_effect=[2, 3, 4] * 4,
        ):
            self.assertTrue(engine._ensure_today_arrival_plan())
            first_plan = [dict(entry) for entry in engine.state.today_arrival_plan]
            self.assertFalse(engine._ensure_today_arrival_plan())

        self.assertEqual(demand_mock.call_count, 1)
        self.assertEqual(engine.state.today_arrival_plan_day, 5)
        self.assertEqual(len(first_plan), 12)
        self.assertEqual(engine.state.today_arrival_plan, first_plan)
        self.assertEqual(sorted({entry["arrival_turn"] for entry in first_plan}), [2, 3, 4])
        self.assertEqual(sum(1 for entry in first_plan if entry["arrival_turn"] == 2), 4)
        self.assertEqual(sum(1 for entry in first_plan if entry["arrival_turn"] == 3), 4)
        self.assertEqual(sum(1 for entry in first_plan if entry["arrival_turn"] == 4), 4)

    def test_empty_demand_only_locks_the_day(self):
        engine = self._new_engine()
        engine.state.day = 9
        engine.state.turn = 1

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ) as demand_mock:
            first = engine._ensure_today_arrival_plan()
            second = engine._ensure_today_arrival_plan()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(demand_mock.call_count, 1)
        self.assertEqual(engine.state.today_arrival_plan_day, 9)
        self.assertEqual(engine.state.today_arrival_plan, [])

    def test_planned_arrivals_use_turns_2_3_4_and_overflow_is_turned_away(self):
        engine = self._new_engine()
        engine.state.day = 3
        engine.state.turn = 1

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 12, "overnight_guest_count": 0},
        ), mock.patch.object(
            CampingPlazaEngine,
            "_roll_arrival_turn",
            side_effect=[2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4],
        ):
            self.assertTrue(engine._ensure_today_arrival_plan())

        plan = engine.state.today_arrival_plan
        self.assertEqual(len(plan), 12)
        self.assertEqual(sorted({entry["arrival_turn"] for entry in plan}), [2, 3, 4])

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine.state.turn = 2
            engine._process_planned_arrivals({"events": []})
            engine._process_planned_arrivals({"events": []})

            engine.state.turn = 3
            engine._process_planned_arrivals({"events": []})

            engine.state.turn = 4
            engine._process_planned_arrivals({"events": []})
            engine._process_planned_arrivals({"events": []})

            engine.state.turn = 5
            engine._process_planned_arrivals({"events": []})

        turn2 = [entry for entry in plan if entry["arrival_turn"] == 2]
        turn3 = [entry for entry in plan if entry["arrival_turn"] == 3]
        turn4 = [entry for entry in plan if entry["arrival_turn"] == 4]

        self.assertEqual([entry["arrival_status"] for entry in turn2], ["arrived"] * 4)
        self.assertEqual([entry["arrival_status"] for entry in turn3], ["arrived"] * 4)
        self.assertEqual([entry["arrival_status"] for entry in turn4].count("arrived"), 2)
        self.assertEqual([entry["arrival_status"] for entry in turn4].count("turned_away_full"), 2)
        self.assertEqual(len(engine.npc_pool), 10)
        self.assertEqual(engine.state.day_campsite_groups_served, 10)
        self.assertTrue(all(entry["arrival_status"] != "pending" for entry in plan))

        before_retry = [entry["arrival_status"] for entry in plan]
        engine.state.turn = 4
        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_planned_arrivals({"events": []})
        self.assertEqual([entry["arrival_status"] for entry in plan], before_retry)
        self.assertEqual(len(engine.npc_pool), 10)

    def test_turn6_to_turn1_sets_reputation_before_demand_calculation(self):
        engine = self._new_engine()
        engine.state.day = 1
        engine.state.turn = 6
        engine.state.day_end_completed = True
        engine.state.pending_reviews = [
            {
                "created_day": 1,
                "rating": 5,
                "npc_id": 88,
                "visit_type": "day",
                "group_size": 2,
            }
        ]

        seen_average_ratings = []

        def capture_demand():
            seen_average_ratings.append(engine.get_average_rating())
            return {"day_guest_count": 3, "overnight_guest_count": 0}

        with mock.patch.object(engine, "_calculate_daily_visitor_demand", side_effect=capture_demand) as demand_mock:
            with mock.patch.object(engine, "_generate_daily_reservation", return_value=None):
                result = engine.start_next_day()

        self.assertEqual(result["day"], 2)
        self.assertEqual(result["turn"], 1)
        self.assertEqual(engine.state.total_reviews, 1)
        self.assertEqual(engine.state.total_rating_sum, 5)
        self.assertEqual(engine.get_average_rating(), 5.0)
        self.assertEqual(seen_average_ratings, [5.0])
        self.assertEqual(demand_mock.call_count, 1)
        self.assertEqual(engine.state.today_arrival_plan_day, 2)
        self.assertEqual(len(engine.state.today_arrival_plan), 3)

    def test_reservation_reuses_planned_identity_and_does_not_double_charge(self):
        engine = self._new_engine()
        reservation = self._auto_generate_overnight_reservation(engine, group_size=2)
        reserved_tent_id = reservation["tent_id"]
        reserved_day = reservation["arrival_day"]
        balance_after_accept = engine.state.balance

        engine.state.day = reserved_day
        arrival_turn_mock, append_actions_mock = self._ensure_overnight_arrival_plan(
            engine,
            arrival_turn=4,
        )

        plan_entry = engine._find_arrival_plan_entry(source="reservation", tent_id=reserved_tent_id)
        self.assertIsNotNone(plan_entry)
        self.assertEqual(arrival_turn_mock.call_count, 1)
        self.assertEqual(append_actions_mock.call_count, 1)
        self.assertEqual(plan_entry["npc_id"], reservation["npc_id"])
        self.assertEqual(plan_entry["group_size"], reservation["group_size"])
        self.assertEqual(plan_entry["arrival_turn"], 4)
        self.assertEqual(plan_entry["arrival_status"], "pending")
        self.assertEqual(plan_entry["economic_level"], reservation["economic_level"])
        self.assertEqual(plan_entry["spending_habit"], reservation["spending_habit"])
        self.assertEqual(plan_entry["temperament"], reservation["temperament"])
        self.assertEqual(plan_entry["tent_id"], reserved_tent_id)
        self.assertTrue(plan_entry["paid"])
        self.assertEqual(engine.state.today_arrival_plan[0]["source"], "reservation")
        self.assertEqual(engine.state.today_arrival_plan[0]["visit_type"], "overnight")
        self.assertEqual(engine.state.today_arrival_plan[0]["tent_id"], reserved_tent_id)
        self.assertEqual(engine.state.today_arrival_plan[0]["paid"], True)
        self.assertEqual(engine.state.reservations, [])
        self.assertTrue(engine._is_today_reserved_tent(reserved_tent_id))
        self.assertEqual(engine.state.balance, balance_after_accept)

        engine.state.turn = 4
        before_arrival_balance = engine.state.balance
        engine._process_planned_arrivals({"events": []})
        self.assertEqual(engine.state.balance, before_arrival_balance)
        self.assertEqual(plan_entry["arrival_status"], "arrived")

        reserved_npcs = [npc for npc in engine.npc_pool if npc.is_reserved]
        self.assertEqual(len(reserved_npcs), 1)
        self.assertEqual(reserved_npcs[0].id, reservation["npc_id"])
        self.assertEqual(reserved_npcs[0].economic_level, reservation["economic_level"])
        self.assertEqual(reserved_npcs[0].spending_habit, reservation["spending_habit"])
        self.assertEqual(reserved_npcs[0].temperament, reservation["temperament"])

        engine._process_planned_arrivals({"events": []})
        self.assertEqual(engine.state.balance, before_arrival_balance)
        self.assertEqual(len([npc for npc in engine.npc_pool if npc.is_reserved]), 1)

    def test_transferred_reservation_allows_new_request_for_third_day(self):
        engine = self._new_engine()
        reservation = self._auto_generate_overnight_reservation(engine, group_size=2)
        reserved_tent_id = reservation["tent_id"]
        engine.state.day = reservation["arrival_day"]

        self._ensure_overnight_arrival_plan(engine, arrival_turn=4)

        self.assertEqual(len(engine.state.today_arrival_plan), 1)
        self.assertEqual(engine.state.reservations, [])
        self.assertEqual(engine.state.today_arrival_plan[0]["source"], "reservation")
        self.assertEqual(engine.state.today_arrival_plan[0]["visit_type"], "overnight")
        self.assertEqual(engine.state.today_arrival_plan[0]["paid"], True)

        balance_before_second_request = engine.state.balance
        self._auto_generate_overnight_reservation(engine, group_size=2)

        self.assertEqual(len(engine.state.reservations), 1)
        self.assertEqual(engine.state.reservations[0]["group_size"], 2)
        self.assertEqual(engine.state.reservations[0]["arrival_day"], engine.state.day + 1)
        self.assertEqual(len(engine.state.today_arrival_plan), 1)
        self.assertGreater(engine.state.balance, balance_before_second_request)

    def test_reservation_entry_uses_shared_arrival_turn_helper(self):
        engine = self._new_engine()
        reservation = self._auto_generate_overnight_reservation(engine, group_size=2)
        engine.state.day = reservation["arrival_day"]

        arrival_turn_mock, append_actions_mock = self._ensure_overnight_arrival_plan(
            engine,
            arrival_turn=3,
            day_guest_count=1,
        )

        self.assertEqual(arrival_turn_mock.call_count, 2)
        self.assertEqual(append_actions_mock.call_count, 2)
        self.assertEqual(
            [(entry["source"], entry["arrival_turn"]) for entry in engine.state.today_arrival_plan],
            [("reservation", 3), ("natural_day", 4)],
        )
        reservation_entry = engine.state.today_arrival_plan[0]
        self.assertEqual(reservation_entry["npc_id"], reservation["npc_id"])
        self.assertEqual(reservation_entry["group_size"], reservation["group_size"])
        self.assertEqual(reservation_entry["tent_id"], reservation["tent_id"])
        self.assertTrue(reservation_entry["paid"])
        self.assertEqual(reservation_entry["economic_level"], reservation["economic_level"])
        self.assertEqual(reservation_entry["spending_habit"], reservation["spending_habit"])
        self.assertEqual(reservation_entry["temperament"], reservation["temperament"])
        self.assertEqual(engine.state.today_arrival_plan[1]["source"], "natural_day")
        self.assertEqual(engine.state.today_arrival_plan[1]["arrival_turn"], 4)

    def test_reservation_arrival_turn_can_use_turns_two_three_and_four(self):
        for arrival_turn in (2, 3, 4):
            with self.subTest(arrival_turn=arrival_turn):
                engine = self._new_engine()
                reservation = self._auto_generate_overnight_reservation(engine, group_size=2)
                engine.state.day = reservation["arrival_day"]

                arrival_turn_mock, _ = self._ensure_overnight_arrival_plan(
                    engine,
                    arrival_turn=arrival_turn,
                )

                self.assertEqual(arrival_turn_mock.call_count, 1)
                self.assertEqual(engine.state.today_arrival_plan[0]["arrival_turn"], arrival_turn)
                self.assertEqual(engine.state.today_arrival_plan[0]["source"], "reservation")
                self.assertEqual(engine.state.today_arrival_plan[0]["tent_id"], reservation["tent_id"])

    def test_nature_observation_is_a_separate_hidden_plan_not_a_planned_action(self):
        engine = self._new_engine()
        engine.state.nature_observation_station_built = True
        engine.state.day = 4
        engine.state.turn = 1

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ), mock.patch.object(CampingPlazaEngine, "_roll_arrival_turn", return_value=3), \
             mock.patch("game_engine.random.randrange", side_effect=[0, 2600]), \
             mock.patch("game_engine.random.choice", return_value=5):
            self.assertTrue(engine._ensure_today_arrival_plan())

        entry = engine.state.today_arrival_plan[0]
        self.assertEqual(entry["observation_plan"]["planned_turn"], 5)
        self.assertGreaterEqual(entry["observation_plan"]["planned_turn"], entry["arrival_turn"])
        self.assertFalse(any(action.get("action") == "observation" for action in entry["planned_actions"]))
