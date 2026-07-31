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
        engine.state.pending_reviews = [
            {
                "created_day": 1,
                "rating": 5,
                "npc_id": 88,
                "visit_type": "day",
                "group_size": 2,
            }
        ]

        seen_rates = []

        def capture_demand():
            seen_rates.append(engine.state.reputation_rate)
            return {"day_guest_count": 3, "overnight_guest_count": 0}

        with mock.patch.object(engine, "_calculate_daily_visitor_demand", side_effect=capture_demand) as demand_mock:
            with mock.patch.object(engine, "_generate_daily_reservation", return_value=None):
                result = engine.advance_turn()

        self.assertEqual(result["day"], 2)
        self.assertEqual(result["turn"], 1)
        self.assertEqual(engine.state.total_reviews, 1)
        self.assertEqual(engine.state.total_rating_sum, 5)
        self.assertEqual(engine.state.reputation_rate, 100.0)
        self.assertEqual(seen_rates, [100.0])
        self.assertEqual(demand_mock.call_count, 1)
        self.assertEqual(engine.state.today_arrival_plan_day, 2)
        self.assertEqual(len(engine.state.today_arrival_plan), 3)

    def test_reservation_reuses_planned_identity_and_does_not_double_charge(self):
        engine = self._new_engine()
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 0,
        }

        accept = engine.accept_reservation(2)
        self.assertTrue(accept["success"])

        reserved_tent_id = engine.state.reserved_tent_id
        reserved_day = engine.state.reserved_tent_day
        reservation_tags = {
            "economic_level": engine.state.reservation["economic_level"],
            "spending_habit": engine.state.reservation["spending_habit"],
            "temperament": engine.state.reservation["temperament"],
        }
        balance_after_accept = engine.state.balance

        engine.state.day = reserved_day
        engine.tents[reserved_tent_id].status = "reserved"

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ), mock.patch.object(CampingPlazaEngine, "_roll_arrival_turn", return_value=4), mock.patch(
            "game_engine.random.random", side_effect=[0.99, 0.99, 0.99]
        ):
            self.assertTrue(engine._ensure_today_arrival_plan())

        plan_entry = engine._find_arrival_plan_entry(source="reservation", tent_id=reserved_tent_id)
        self.assertIsNotNone(plan_entry)
        reserved_npc_id = plan_entry["npc_id"]
        self.assertIsNone(engine.state.reservation)
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertIsNone(engine.state.reserved_tent_day)
        self.assertEqual(plan_entry["arrival_turn"], 4)
        self.assertEqual(plan_entry["arrival_status"], "pending")
        self.assertEqual(plan_entry["economic_level"], reservation_tags["economic_level"])
        self.assertEqual(plan_entry["spending_habit"], reservation_tags["spending_habit"])
        self.assertEqual(plan_entry["temperament"], reservation_tags["temperament"])
        self.assertTrue(engine._is_today_reserved_tent(reserved_tent_id))
        self.assertEqual(engine.tents[reserved_tent_id].status, "reserved")
        self.assertIsNone(engine._find_available_tent(1))

        engine.state.turn = 1
        engine._process_reservations({"events": []})
        self.assertEqual(plan_entry["arrival_status"], "pending")
        self.assertEqual(len(engine.npc_pool), 0)

        engine.state.turn = 4
        engine._process_reservations({"events": []})
        self.assertEqual(engine.state.balance, balance_after_accept)
        self.assertEqual(plan_entry["arrival_status"], "arrived")

        reserved_npcs = [npc for npc in engine.npc_pool if npc.is_reserved]
        self.assertEqual(len(reserved_npcs), 1)
        self.assertEqual(reserved_npcs[0].id, reserved_npc_id)
        self.assertEqual(reserved_npcs[0].economic_level, reservation_tags["economic_level"])
        self.assertEqual(reserved_npcs[0].spending_habit, reservation_tags["spending_habit"])
        self.assertEqual(reserved_npcs[0].temperament, reservation_tags["temperament"])

        engine._process_reservations({"events": []})
        self.assertEqual(engine.state.balance, balance_after_accept)
        self.assertEqual(len([npc for npc in engine.npc_pool if npc.is_reserved]), 1)

    def test_transferred_reservation_allows_new_request_for_third_day(self):
        engine = self._new_engine()
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 0,
        }

        self.assertTrue(engine.accept_reservation(2)["success"])
        reserved_tent_id = engine.state.reserved_tent_id
        engine.state.day = 2
        engine.state.turn = 1
        engine.tents[reserved_tent_id].status = "reserved"

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ), mock.patch.object(CampingPlazaEngine, "_roll_arrival_turn", return_value=4), mock.patch(
            "game_engine.random.random", side_effect=[0.99, 0.99, 0.99]
        ):
            self.assertTrue(engine._ensure_today_arrival_plan())

        self.assertEqual(len(engine.state.today_arrival_plan), 1)
        self.assertIsNone(engine.state.reservation)
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertIsNone(engine.state.reserved_tent_day)

        with mock.patch("game_engine.random.random", return_value=0.0), mock.patch(
            "game_engine.random.randint", side_effect=[3, 2, 1, 0]
        ):
            engine._generate_daily_reservation()

        self.assertIsNotNone(engine.state.reservation)
        self.assertEqual(engine.state.reservation["group_size"], 3)
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertIsNone(engine.state.reserved_tent_day)
        self.assertEqual(len(engine.state.today_arrival_plan), 1)

    def test_reservation_entry_uses_shared_arrival_turn_helper(self):
        engine = self._new_engine()
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 0,
        }
        self.assertTrue(engine.accept_reservation(2)["success"])
        reserved_tent_id = engine.state.reserved_tent_id
        engine.state.day = engine.state.reserved_tent_day
        engine.state.turn = 1
        engine.tents[reserved_tent_id].status = "reserved"

        guest = self._make_guest(901, "day")
        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ), mock.patch.object(
            CampingPlazaEngine, "_create_day_guest", return_value=guest
        ), mock.patch.object(
            engine, "_append_planned_actions", wraps=engine._append_planned_actions
        ) as append_actions_mock, mock.patch.object(
            CampingPlazaEngine,
            "_roll_arrival_turn",
            side_effect=[3, 4],
        ) as arrival_turn_mock, mock.patch(
            "game_engine.random.random",
            side_effect=[0.99, 0.99, 0.99, 0.99, 0.99, 0.99],
        ):
            self.assertTrue(engine._ensure_today_arrival_plan())

        self.assertEqual(arrival_turn_mock.call_count, 2)
        self.assertEqual(append_actions_mock.call_count, 2)
        self.assertEqual(
            [(entry["source"], entry["arrival_turn"]) for entry in engine.state.today_arrival_plan],
            [("natural_day", 3), ("reservation", 4)],
        )

    def test_reservation_arrival_turn_can_use_turns_two_three_and_four(self):
        for arrival_turn in (2, 3, 4):
            with self.subTest(arrival_turn=arrival_turn):
                engine = self._new_engine()
                engine.state.reservation = {
                    "group_size": 2,
                    "economic_level": 1,
                    "spending_habit": 1,
                    "temperament": 0,
                }
                self.assertTrue(engine.accept_reservation(2)["success"])
                reserved_tent_id = engine.state.reserved_tent_id
                engine.state.day = engine.state.reserved_tent_day
                engine.state.turn = 1
                engine.tents[reserved_tent_id].status = "reserved"

                with mock.patch.object(
                    CampingPlazaEngine,
                    "_calculate_daily_visitor_demand",
                    return_value={"day_guest_count": 0, "overnight_guest_count": 0},
                ), mock.patch.object(
                    CampingPlazaEngine, "_roll_arrival_turn", return_value=arrival_turn
                ), mock.patch(
                    "game_engine.random.random", side_effect=[0.99, 0.99, 0.99]
                ):
                    self.assertTrue(engine._ensure_today_arrival_plan())

                self.assertEqual(engine.state.today_arrival_plan[0]["arrival_turn"], arrival_turn)
