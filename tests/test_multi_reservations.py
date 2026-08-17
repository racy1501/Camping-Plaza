import os
import sys
import unittest
import uuid
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine
import game_api


class MultiReservationTests(unittest.TestCase):
    def setUp(self):
        self.db_path = os.path.join(
            _PROJECT_ROOT, f".test_multi_reservations_{uuid.uuid4().hex}.sqlite"
        )
        self.addCleanup(self._cleanup_db)
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self.engine.state.daily_demand_profile = {
            "natural_day_group_demand": 0,
            "natural_overnight_group_demand": 0,
            "reservations_processed": False,
        }
        self.engine.state.daily_demand_profile_day = self.engine.state.day

    def _cleanup_db(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    def _generate(self, random_rolls, group_sizes):
        with mock.patch.object(self.engine, "_assign_hidden_tags"), \
             mock.patch("game_engine.random.random", side_effect=random_rolls), \
             mock.patch("game_engine.random.randint", side_effect=group_sizes):
            self.engine._generate_daily_reservation()

    def test_zero_daytime_and_overnight_reservations_is_valid(self):
        self._generate([0.99] * 11, [])

        self.assertEqual(self.engine.state.reservations, [])
        self.assertTrue(self.engine.state.daily_demand_profile["reservations_processed"])

    def test_daytime_slots_roll_independently_and_charge_each_success(self):
        balance_before = self.engine.state.balance
        self._generate([0.1, 0.99, 0.1] + [0.99] * 8, [2, 5])

        reservations = self.engine.state.reservations
        self.assertEqual(len(reservations), 2)
        self.assertTrue(all(item["visit_type"] == "day" for item in reservations))
        self.assertEqual([item["group_size"] for item in reservations], [2, 5])
        self.assertEqual(
            self.engine.state.today_income["campsite"],
            self.engine.CAMPSITE_FEE * 2,
        )
        self.assertEqual(
            self.engine.state.balance,
            balance_before + self.engine.CAMPSITE_FEE * 2,
        )

    def test_overnight_reservations_use_distinct_smallest_suitable_tents(self):
        for tent_id in (2, 3):
            self.engine.tents[tent_id].is_unlocked = True

        self._generate([0.99] * 10 + [0.1, 0.1, 0.1], [1, 2, 2])

        reservations = [
            item for item in self.engine.state.reservations
            if item["visit_type"] == "overnight"
        ]
        self.assertEqual([item["tent_id"] for item in reservations], [1, 2, 3])
        self.assertEqual(len({item["tent_id"] for item in reservations}), 3)

    def test_oversized_overnight_request_is_missed_without_charge_or_natural_demand_change(self):
        demand_before = dict(self.engine._calculate_daily_visitor_demand())
        balance_before = self.engine.state.balance
        income_before = dict(self.engine.state.today_income)

        self._generate([0.99] * 10 + [0.1], [6])

        self.assertEqual(self.engine.state.reservations, [])
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.state.today_income, income_before)
        self.assertEqual(self.engine._calculate_daily_visitor_demand(), demand_before)

    def test_next_day_converts_all_reservations_once_without_recharging(self):
        self._generate([0.1, 0.1] + [0.99] * 9, [2, 3])
        balance_after_reservation = self.engine.state.balance
        self.engine.state.day = 2
        self.engine.state.today_arrival_plan_day = 0
        self.engine.state.daily_demand_profile = {
            "natural_day_group_demand": 0,
            "natural_overnight_group_demand": 0,
            "reservations_processed": False,
        }
        self.engine.state.daily_demand_profile_day = 2

        with mock.patch.object(self.engine, "_roll_arrival_turn", return_value=2):
            self.assertTrue(self.engine._ensure_today_arrival_plan())

        entries = self.engine.state.today_arrival_plan
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(entry["source"] == "reservation" for entry in entries))
        self.assertTrue(all(entry["paid"] is True for entry in entries))
        self.assertEqual(self.engine.state.reservations, [])
        self.assertEqual(self.engine.state.balance, balance_after_reservation)
        self.assertFalse(self.engine._ensure_today_arrival_plan())

        self.engine.state.turn = 2
        self.engine._process_planned_arrivals({"events": []})
        self.assertEqual(self.engine.state.balance, balance_after_reservation)
        self.assertEqual(self.engine.state.day_campsite_groups_served, 2)

    def test_pending_daytime_reservations_preoccupy_capacity(self):
        self._generate([0.1, 0.1, 0.1] + [0.99] * 8, [1, 2, 3])
        self.engine.state.day = 2
        self.engine.state.today_arrival_plan_day = 0
        self.engine.state.daily_demand_profile = {
            "natural_day_group_demand": 0,
            "natural_overnight_group_demand": 0,
            "reservations_processed": False,
        }
        self.engine.state.daily_demand_profile_day = 2

        with mock.patch.object(self.engine, "_roll_arrival_turn", return_value=3):
            self.engine._ensure_today_arrival_plan()

        self.assertEqual(self.engine.get_day_campsite_remaining(), 7)

    def test_overnight_reservation_keeps_its_tent_and_is_not_recharged_on_arrival(self):
        self._generate([0.99] * 10 + [0.1], [2])
        balance_after_reservation = self.engine.state.balance
        self.engine.state.day = 2
        self.engine.state.today_arrival_plan_day = 0
        self.engine.state.daily_demand_profile = {
            "natural_day_group_demand": 0,
            "natural_overnight_group_demand": 0,
            "reservations_processed": False,
        }
        self.engine.state.daily_demand_profile_day = 2

        with mock.patch.object(self.engine, "_roll_arrival_turn", return_value=2):
            self.engine._ensure_today_arrival_plan()
        self.engine._assign_reserved_tents_for_today()

        entry = self.engine.state.today_arrival_plan[0]
        self.assertEqual(entry["tent_id"], 1)
        self.assertEqual(self.engine.tents[1].status, "reserved")
        self.assertIsNone(self.engine._find_available_tent(1))

        self.engine.state.turn = 2
        self.engine._process_planned_arrivals({"events": []})

        self.assertEqual(self.engine.tents[1].occupied_by, entry["npc_id"])
        self.assertEqual(self.engine.state.balance, balance_after_reservation)

    def test_reservations_roundtrip_through_existing_snapshot(self):
        self._generate([0.1] + [0.99] * 10, [4])
        expected = [dict(item) for item in self.engine.state.reservations]
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.reservations, expected)

    def test_api_and_mcp_state_expose_sanitized_reservations_list(self):
        self._generate([0.1] + [0.99] * 10, [4])
        original_engine = game_api.engine
        game_api.engine = self.engine
        self.addCleanup(setattr, game_api, "engine", original_engine)

        api_state = game_api.get_state()
        mcp_state = game_api.mcp_state()

        self.assertIn("reservations", api_state)
        self.assertNotIn("reservation", api_state)
        self.assertEqual(len(api_state["reservations"]), 1)
        self.assertEqual(
            api_state["reservations"][0],
            {
                "group_size": 4,
                "visit_type": "day",
                "arrival_day": self.engine.state.day + 1,
                "status": "accepted",
            },
        )
        self.assertNotIn("reservations", mcp_state)
        self.assertNotIn("confirmed_reservations", mcp_state)


if __name__ == "__main__":
    unittest.main()
