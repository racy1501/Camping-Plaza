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
        _PROJECT_ROOT, f".dining_phase2a_{uuid.uuid4().hex}.sqlite"
    )


def _cleanup_db_path(db_path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = db_path + suffix if suffix else db_path
        if os.path.exists(path):
            os.remove(path)


class DiningPlannedActionsPhase2ATests(unittest.TestCase):
    def _new_engine(self):
        db_path = _temp_db_path()
        self.addCleanup(_cleanup_db_path, db_path)
        engine = CampingPlazaEngine(db_path=db_path)
        engine.state.today_arrival_plan_day = 0
        engine.state.today_arrival_plan = []
        return engine

    def _make_guest(
        self,
        engine,
        npc_id: int,
        *,
        visit_type: str,
        economic_level: int = 1,
        spending_habit: int = 1,
        total_satisfaction: int = 60,
        location: str = "campsite",
    ):
        npc = NPCGroup(
            id=npc_id,
            group_size=2,
            visit_type=visit_type,
            total_satisfaction=total_satisfaction,
            location=location,
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
        source: str,
        arrival_status: str = "arrived",
    ):
        entry = engine._build_arrival_plan_entry(npc, arrival_turn, source)
        entry["arrival_status"] = arrival_status
        return entry

    def _add_dining_action(
        self,
        entry: dict,
        *,
        planned_turn: int,
        preferred_menu: str,
        status: str = "pending",
    ):
        entry["planned_actions"].append(
            {
                "action": "dining",
                "planned_turn": planned_turn,
                "preferred_menu": preferred_menu,
                "status": status,
            }
        )
        return entry["planned_actions"][-1]

    def test_daily_plan_draws_dining_once_and_stores_menu_turn(self):
        engine = self._new_engine()
        engine.facilities["entertainment"].level = 1
        engine.state.day = 3
        engine.state.turn = 1

        guest_a = self._make_guest(
            engine, 101, visit_type="day", economic_level=0, spending_habit=1
        )
        guest_b = self._make_guest(
            engine, 202, visit_type="overnight", economic_level=2, spending_habit=0
        )

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 1},
        ):
            with mock.patch.object(
                CampingPlazaEngine, "_create_day_guest", return_value=guest_a
            ):
                with mock.patch.object(
                    CampingPlazaEngine,
                    "_create_overnight_guest",
                    return_value=guest_b,
                ):
                    with mock.patch(
                        "game_engine.random.random",
                        side_effect=[0.0, 0.99, 0.99, 0.99, 0.99, 0.99],
                    ) as random_mock:
                        with mock.patch(
                            "game_engine.random.sample", return_value=[5]
                        ) as sample_mock:
                            first = engine._ensure_today_arrival_plan()
                            second = engine._ensure_today_arrival_plan()

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(random_mock.call_count, 6)
        self.assertEqual(sample_mock.call_count, 1)
        self.assertEqual(len(engine.state.today_arrival_plan), 2)

        first_entry = engine.state.today_arrival_plan[0]
        second_entry = engine.state.today_arrival_plan[1]
        self.assertEqual(first_entry["arrival_turn"], 2)
        self.assertEqual(second_entry["arrival_turn"], 3)
        self.assertEqual(len(first_entry["planned_actions"]), 1)
        self.assertEqual(second_entry["planned_actions"], [])
        self.assertEqual(
            first_entry["planned_actions"][0]["preferred_menu"], "basic"
        )
        self.assertEqual(first_entry["planned_actions"][0]["planned_turn"], 5)

    def test_single_planned_action_can_be_scheduled(self):
        engine = self._new_engine()
        actions = [{"action": "dining", "status": "pending"}]

        with mock.patch("game_engine.random.shuffle") as shuffle_mock:
            with mock.patch("game_engine.random.sample", return_value=[5]) as sample_mock:
                scheduled = engine._schedule_planned_actions(2, actions)

        self.assertIs(scheduled, actions)
        self.assertEqual(actions[0]["planned_turn"], 5)
        shuffle_mock.assert_called_once()
        self.assertEqual(sample_mock.call_args.args, ([2, 3, 4, 5], 1))

    def test_two_planned_actions_receive_distinct_turns(self):
        engine = self._new_engine()
        actions = [
            {"action": "dining", "status": "pending"},
            {"action": "mock_action", "status": "pending"},
        ]

        with mock.patch("game_engine.random.shuffle"):
            with mock.patch("game_engine.random.sample", return_value=[2, 5]):
                engine._schedule_planned_actions(2, actions)

        self.assertEqual({action["planned_turn"] for action in actions}, {2, 5})
        self.assertEqual(
            [action["planned_turn"] for action in actions],
            sorted(action["planned_turn"] for action in actions),
        )

    def test_two_planned_actions_order_can_be_controlled(self):
        engine = self._new_engine()
        first_action = {"action": "dining", "status": "pending"}
        second_action = {"action": "mock_action", "status": "pending"}
        actions = [first_action, second_action]

        def reverse_actions(items):
            items.reverse()

        with mock.patch("game_engine.random.shuffle", side_effect=reverse_actions):
            with mock.patch("game_engine.random.sample", return_value=[4, 2]):
                engine._schedule_planned_actions(2, actions)

        self.assertEqual(second_action["planned_turn"], 2)
        self.assertEqual(first_action["planned_turn"], 4)
        self.assertEqual(actions, [second_action, first_action])

    def test_turn_four_arrival_limits_two_actions_to_turns_four_and_five(self):
        engine = self._new_engine()
        actions = [
            {"action": "dining", "status": "pending"},
            {"action": "mock_action", "status": "pending"},
        ]

        with mock.patch("game_engine.random.shuffle"):
            with mock.patch("game_engine.random.sample", return_value=[5, 4]) as sample_mock:
                engine._schedule_planned_actions(4, actions)

        self.assertEqual({action["planned_turn"] for action in actions}, {4, 5})
        self.assertEqual(sample_mock.call_args.args, ([4, 5], 2))

    def test_scheduling_fails_when_actions_exceed_available_turns(self):
        engine = self._new_engine()
        actions = [
            {"action": "a", "status": "pending"},
            {"action": "b", "status": "pending"},
            {"action": "c", "status": "pending"},
        ]

        with self.assertRaisesRegex(
            ValueError, "planned action count exceeds available turns"
        ):
            engine._schedule_planned_actions(4, actions)

    def test_day_guest_arrives_to_campsite_not_random_consumption_area(self):
        engine = self._new_engine()
        engine.state.day = 4
        engine.state.turn = 2
        guest = self._make_guest(engine, 301, visit_type="day")
        entry = self._make_entry(
            engine,
            guest,
            arrival_turn=2,
            source="natural_day",
            arrival_status="pending",
        )
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        result = {"events": []}
        engine._process_planned_arrivals(result)

        self.assertEqual(entry["arrival_status"], "arrived")
        self.assertEqual(len(engine.npc_pool), 1)
        self.assertEqual(engine.npc_pool[0].location, "campsite")

    def test_dining_action_runs_only_on_planned_turn(self):
        engine = self._new_engine()
        engine.state.day = 5
        npc = self._make_guest(engine, 401, visit_type="day", location="campsite")
        engine.npc_pool.append(npc)
        entry = self._make_entry(
            engine, npc, arrival_turn=2, source="natural_day", arrival_status="arrived"
        )
        action = self._add_dining_action(
            entry, planned_turn=3, preferred_menu="standard"
        )
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2

        engine.state.turn = 2
        engine._process_dining({"events": []})
        self.assertEqual(action["status"], "pending")
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.location, "campsite")

        engine.state.turn = 3
        result = {"events": []}
        engine._process_dining(result)

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["result"], "success")
        self.assertEqual(npc.location, "dining")
        self.assertEqual(engine.state.today_income["dining"], 90)
        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(npc.total_satisfaction, 64)
        self.assertEqual(len(result["events"]), 1)

    def test_overnight_guest_can_execute_dining_action_with_menu_downgrade(self):
        engine = self._new_engine()
        engine.state.day = 6
        engine.state.turn = 4
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2

        npc = self._make_guest(
            engine,
            501,
            visit_type="overnight",
            economic_level=2,
            location="tent_1",
        )
        engine.npc_pool.append(npc)
        entry = self._make_entry(
            engine,
            npc,
            arrival_turn=4,
            source="natural_overnight",
            arrival_status="arrived",
        )
        action = self._add_dining_action(
            entry, planned_turn=4, preferred_menu="premium"
        )
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        engine._process_dining({"events": []})

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["actual_menu"], "standard")
        self.assertEqual(engine.state.today_income["dining"], 90)
        self.assertEqual(npc.location, "dining")
        self.assertEqual(npc.total_satisfaction, 64)

    def test_insufficient_food_fails_atomically_and_does_not_retry(self):
        engine = self._new_engine()
        engine.state.day = 7
        engine.state.turn = 2
        engine.state.food_stock = 1
        engine.state.balance = 500

        npc = self._make_guest(
            engine,
            601,
            visit_type="day",
            economic_level=2,
            total_satisfaction=55,
        )
        engine.npc_pool.append(npc)
        entry = self._make_entry(
            engine, npc, arrival_turn=2, source="natural_day", arrival_status="arrived"
        )
        action = self._add_dining_action(
            entry, planned_turn=2, preferred_menu="premium"
        )
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        result = {"events": []}
        engine._process_dining(result)

        self.assertEqual(action["status"], "failed")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(engine.state.food_stock, 1)
        self.assertEqual(engine.state.balance, 500)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 55)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(len(result["events"]), 1)

        engine.state.turn = 3
        engine._process_dining({"events": []})
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(action["status"], "failed")

    def test_not_arrived_or_missing_guest_actions_are_skipped_without_charge(self):
        engine = self._new_engine()
        engine.state.day = 8
        engine.state.turn = 2

        pending_guest = self._make_guest(engine, 701, visit_type="day")
        pending_entry = self._make_entry(
            engine,
            pending_guest,
            arrival_turn=2,
            source="natural_day",
            arrival_status="pending",
        )
        pending_action = self._add_dining_action(
            pending_entry, planned_turn=2, preferred_menu="standard"
        )

        missing_guest = self._make_guest(engine, 702, visit_type="day")
        missing_entry = self._make_entry(
            engine,
            missing_guest,
            arrival_turn=2,
            source="natural_day",
            arrival_status="arrived",
        )
        missing_action = self._add_dining_action(
            missing_entry, planned_turn=2, preferred_menu="standard"
        )

        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [pending_entry, missing_entry]
        engine.state.balance = 700

        engine._process_dining({"events": []})

        self.assertEqual(pending_action["status"], "skipped")
        self.assertEqual(pending_action["result"], "not_arrived")
        self.assertEqual(missing_action["status"], "skipped")
        self.assertEqual(missing_action["result"], "missing_npc")
        self.assertEqual(engine.state.balance, 700)
        self.assertEqual(engine.state.today_income["dining"], 0)

    def test_same_group_cannot_be_charged_twice_in_one_day(self):
        engine = self._new_engine()
        engine.state.day = 9
        engine.state.food_stock = 4

        npc = self._make_guest(engine, 801, visit_type="day", location="campsite")
        engine.npc_pool.append(npc)
        entry = self._make_entry(
            engine, npc, arrival_turn=2, source="natural_day", arrival_status="arrived"
        )
        first_action = self._add_dining_action(
            entry, planned_turn=2, preferred_menu="standard"
        )
        second_action = self._add_dining_action(
            entry, planned_turn=4, preferred_menu="standard"
        )
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]
        engine.facilities["dining"].level = 1

        engine.state.turn = 2
        engine._process_dining({"events": []})
        engine.state.turn = 4
        engine._process_dining({"events": []})

        self.assertEqual(first_action["status"], "completed")
        self.assertEqual(second_action["status"], "skipped")
        self.assertEqual(second_action["result"], "already_dined")
        self.assertEqual(engine.state.today_income["dining"], 90)

    def test_same_turn_dining_action_does_not_settle_twice(self):
        engine = self._new_engine()
        engine.state.day = 11
        engine.state.turn = 3
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2
        engine.state.balance = 1000

        npc = self._make_guest(engine, 851, visit_type="day", location="campsite")
        engine.npc_pool.append(npc)
        entry = self._make_entry(
            engine,
            npc,
            arrival_turn=3,
            source="natural_day",
            arrival_status="arrived",
        )
        action = self._add_dining_action(
            entry, planned_turn=3, preferred_menu="standard"
        )
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [entry]

        first_result = {"events": []}
        engine._process_dining(first_result)
        first_balance = engine.state.balance
        first_income = engine.state.today_income["dining"]
        first_food_stock = engine.state.food_stock
        first_satisfaction = npc.total_satisfaction
        first_last_dining_day = npc.last_dining_day

        second_result = {"events": []}
        engine._process_dining(second_result)

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["result"], "success")
        self.assertEqual(first_balance, 1090)
        self.assertEqual(first_income, 90)
        self.assertEqual(first_food_stock, 0)
        self.assertEqual(first_satisfaction, 64)
        self.assertEqual(first_last_dining_day, engine.state.day)
        self.assertEqual(engine.state.balance, first_balance)
        self.assertEqual(engine.state.today_income["dining"], first_income)
        self.assertEqual(engine.state.food_stock, first_food_stock)
        self.assertEqual(npc.total_satisfaction, first_satisfaction)
        self.assertEqual(npc.last_dining_day, first_last_dining_day)
        self.assertEqual(first_result["events"], [
            "客组851购买中档套餐，2人用餐，收入+90，消耗食材2份，整组满意度+4"
        ])
        self.assertEqual(second_result["events"], [])

    def test_phase1_capacity_rules_still_hold_when_dining_actions_exist(self):
        engine = self._new_engine()
        engine.state.day = 10
        engine.state.turn = 2
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.food_stock = 30

        plan = []
        for index in range(11):
            npc = self._make_guest(engine, 900 + index, visit_type="day")
            entry = self._make_entry(
                engine,
                npc,
                arrival_turn=2,
                source="natural_day",
                arrival_status="pending",
            )
            self._add_dining_action(entry, planned_turn=2, preferred_menu="standard")
            plan.append(entry)
        engine.state.today_arrival_plan = plan

        result = {"events": []}
        engine._process_planned_arrivals(result)
        engine._process_dining(result)

        arrived_entries = [
            entry for entry in plan if entry["arrival_status"] == "arrived"
        ]
        turned_away_entries = [
            entry for entry in plan if entry["arrival_status"] == "turned_away_full"
        ]
        self.assertEqual(len(arrived_entries), 10)
        self.assertEqual(len(turned_away_entries), 1)
        self.assertEqual(engine.state.day_campsite_groups_served, 10)
        self.assertEqual(turned_away_entries[0]["planned_actions"][0]["status"], "skipped")
        self.assertEqual(
            turned_away_entries[0]["planned_actions"][0]["result"], "not_arrived"
        )

    def test_reserved_guest_dining_action_reuses_id_and_skips_lodging_charge(self):
        engine = self._new_engine()
        engine.state.day = 1
        engine.state.turn = 1
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 1,
        }

        reservation_result = engine.accept_reservation(2)
        self.assertTrue(reservation_result["success"])
        balance_after_reservation = engine.state.balance
        accommodation_income_after_reservation = engine.state.today_income["accommodation"]

        engine.state.day = 2
        engine.state.turn = 2
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ):
            with mock.patch(
                "game_engine.random.random", side_effect=[0.0, 0.99, 0.99]
            ):
                with mock.patch("game_engine.random.sample", return_value=[2]):
                    self.assertTrue(engine._ensure_today_arrival_plan())

        self.assertEqual(len(engine.state.today_arrival_plan), 1)
        plan_entry = engine.state.today_arrival_plan[0]
        dining_action = plan_entry["planned_actions"][0]

        self.assertEqual(plan_entry["source"], "reservation")
        self.assertEqual(plan_entry["arrival_turn"], 2)
        self.assertEqual(dining_action["action"], "dining")
        self.assertGreaterEqual(dining_action["planned_turn"], plan_entry["arrival_turn"])
        self.assertLessEqual(dining_action["planned_turn"], 5)
        self.assertEqual(dining_action["status"], "pending")
        self.assertIsNotNone(plan_entry["npc_id"])

        result = {"events": []}
        engine._process_reservations(result)

        self.assertEqual(plan_entry["arrival_status"], "arrived")
        self.assertEqual(len(engine.npc_pool), 1)
        self.assertEqual(engine.npc_pool[0].id, plan_entry["npc_id"])
        self.assertTrue(engine.npc_pool[0].is_reserved)
        self.assertTrue(engine.npc_pool[0].paid)
        self.assertEqual(engine.npc_pool[0].location, "tent_1")
        self.assertEqual(engine.state.balance, balance_after_reservation)
        self.assertEqual(
            engine.state.today_income["accommodation"],
            accommodation_income_after_reservation,
        )

        engine._process_dining(result)

        self.assertEqual(dining_action["status"], "completed")
        self.assertEqual(dining_action["result"], "success")
        self.assertEqual(engine.npc_pool[0].location, "dining")
        self.assertEqual(engine.state.today_income["dining"], 90)
        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(engine.npc_pool[0].last_dining_day, engine.state.day)
        self.assertEqual(engine.npc_pool[0].total_satisfaction, 74)
        self.assertEqual(
            engine.state.balance,
            balance_after_reservation + engine.state.today_income["dining"],
        )

    def test_reserved_guest_dining_action_survives_save_and_reload(self):
        db_path = _temp_db_path()
        self.addCleanup(_cleanup_db_path, db_path)

        engine = CampingPlazaEngine(db_path=db_path)
        engine.state.day = 1
        engine.state.turn = 1
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 1,
        }

        self.assertTrue(engine.accept_reservation(2)["success"])
        balance_after_reservation = engine.state.balance
        accommodation_income_after_reservation = engine.state.today_income["accommodation"]

        engine.state.day = 2
        engine.state.turn = 2
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2

        with mock.patch.object(
            CampingPlazaEngine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ):
            with mock.patch(
                "game_engine.random.random", side_effect=[0.0, 0.99, 0.99]
            ):
                with mock.patch("game_engine.random.sample", return_value=[2]):
                    self.assertTrue(engine._ensure_today_arrival_plan())

        plan_entry = engine.state.today_arrival_plan[0]
        dining_action = plan_entry["planned_actions"][0]
        reserved_npc_id = plan_entry["npc_id"]
        self.assertEqual(plan_entry["source"], "reservation")
        self.assertEqual(plan_entry["npc_id"], reserved_npc_id)
        self.assertEqual(plan_entry["arrival_turn"], 2)
        self.assertEqual(plan_entry["arrival_status"], "pending")
        self.assertEqual(dining_action["action"], "dining")
        self.assertGreaterEqual(dining_action["planned_turn"], plan_entry["arrival_turn"])
        self.assertLessEqual(dining_action["planned_turn"], 5)
        self.assertEqual(dining_action["preferred_menu"], "standard")
        self.assertEqual(dining_action["status"], "pending")

        self.assertTrue(engine.save_state())

        reloaded = CampingPlazaEngine(db_path=db_path)
        self.assertEqual(len(reloaded.state.today_arrival_plan), 1)

        reloaded_entry = reloaded.state.today_arrival_plan[0]
        reloaded_action = reloaded_entry["planned_actions"][0]
        self.assertEqual(reloaded_entry["source"], "reservation")
        self.assertEqual(reloaded_entry["npc_id"], reserved_npc_id)
        self.assertEqual(reloaded_entry["arrival_turn"], 2)
        self.assertEqual(reloaded_entry["arrival_status"], "pending")
        self.assertEqual(reloaded_action["action"], "dining")
        self.assertEqual(reloaded_action["planned_turn"], dining_action["planned_turn"])
        self.assertEqual(reloaded_action["preferred_menu"], "standard")
        self.assertEqual(reloaded_action["status"], "pending")

        reloaded.state.turn = 2
        result = {"events": []}
        reloaded._process_reservations(result)
        balance_after_checkin = reloaded.state.balance
        accommodation_income_after_checkin = reloaded.state.today_income["accommodation"]
        reloaded._process_dining(result)

        self.assertEqual(reloaded_entry["arrival_status"], "arrived")
        self.assertEqual(reloaded_action["status"], "completed")
        self.assertEqual(reloaded_action["result"], "success")
        self.assertEqual(len(reloaded.npc_pool), 1)
        self.assertEqual(reloaded.npc_pool[0].id, reserved_npc_id)
        self.assertTrue(reloaded.npc_pool[0].is_reserved)
        self.assertTrue(reloaded.npc_pool[0].paid)
        self.assertEqual(reloaded.npc_pool[0].location, "dining")
        self.assertEqual(balance_after_checkin, balance_after_reservation)
        self.assertEqual(
            accommodation_income_after_checkin,
            accommodation_income_after_reservation,
        )
        self.assertEqual(reloaded.state.today_income["dining"], 90)
        self.assertEqual(
            reloaded.state.balance,
            balance_after_reservation + reloaded.state.today_income["dining"],
        )
        self.assertEqual(reloaded.state.food_stock, 0)
        self.assertEqual(reloaded.npc_pool[0].last_dining_day, reloaded.state.day)
        self.assertEqual(reloaded.npc_pool[0].total_satisfaction, 74)
