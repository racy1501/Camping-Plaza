import os
import sys
import tempfile
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class HotSpringExecutionTests(unittest.TestCase):
    def setUp(self):
        temp_dir = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
        self.db_path = os.path.join(temp_dir, "hot_spring_execution.sqlite")
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self.engine.state.hot_spring_built = True
        self.engine.state.day = 1
        self.engine.state.turn = 2
        self.engine.state.today_arrival_plan_day = 1
        self.engine.state.today_arrival_plan = []
        self.engine.state.today_conflict_event = {"status": "no_event"}

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def _add_guest(self, *, group_size=2, visit_type="day", source="natural_day", planned_turn=None, temperament=0):
        npc = NPCGroup(
            id=self.engine._next_npc_id(),
            group_size=group_size,
            visit_type=visit_type,
            location="campsite",
            total_satisfaction=50,
            temperament=temperament,
        )
        self.engine.npc_pool.append(npc)
        action = {
            "action": "hot_spring",
            "planned_turn": self.engine.state.turn if planned_turn is None else planned_turn,
            "status": "pending",
        }
        self.engine.state.today_arrival_plan.append({
            "npc_id": npc.id,
            "planned_day": self.engine.state.day,
            "source": source,
            "visit_type": visit_type,
            "arrival_status": "arrived",
            "planned_actions": [action],
        })
        return npc, action

    def test_success_charges_by_people_and_adds_group_satisfaction(self):
        npc, action = self._add_guest(group_size=2)
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["charged_amount"], 160)
        self.assertEqual(action["people_served"], 2)
        self.assertEqual(self.engine.state.balance, 1160)
        self.assertEqual(self.engine.state.today_income["hot_spring"], 160)
        self.assertEqual(self.engine.state.hot_spring_people_served_today, 2)
        self.assertEqual(npc.total_satisfaction, 56)
        self.assertEqual(npc.location, "hot_spring")

    def test_multiple_hot_spring_actions_are_one_formal_summary(self):
        first, first_action = self._add_guest(group_size=1)
        second, second_action = self._add_guest(group_size=2)
        first.campsite_slot = 1
        second.campsite_slot = 2
        self.engine._process_hot_spring({"events": []})

        logs = [
            item for item in self.engine.state.event_history
            if item.get("event_type") == "hot_spring_completed"
        ]
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["guest_ids"], [first.id, second.id])
        self.assertIn("收入240金币", logs[0]["text"])
        self.assertIn("满意度+12", logs[0]["text"])
        self.assertNotEqual(logs[0]["event_type"], "legacy")

    def test_capacity_can_be_shared_and_exact_capacity_succeeds(self):
        self.engine.state.hot_spring_people_served_today = 18
        npc, action = self._add_guest(group_size=2)
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(action["status"], "completed")
        self.assertEqual(self.engine.state.hot_spring_people_served_today, 20)

    def test_insufficient_capacity_fails_group_atomically(self):
        self.engine.state.hot_spring_people_served_today = 19
        npc, action = self._add_guest(group_size=2)
        balance = self.engine.state.balance
        satisfaction = npc.total_satisfaction
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(action["status"], "failed")
        self.assertEqual(action["result"], "capacity_full")
        self.assertEqual(action["charged_amount"], 0)
        self.assertEqual(action["satisfaction_gain"], 0)
        self.assertEqual(action["people_served"], 0)
        self.assertEqual(self.engine.state.balance, balance)
        self.assertEqual(npc.total_satisfaction, satisfaction)
        self.assertEqual(self.engine.state.hot_spring_people_served_today, 19)

    def test_insufficient_capacity_adds_temperament_reaction_without_state_change(self):
        self.engine.state.hot_spring_people_served_today = 19
        npc, action = self._add_guest(group_size=2, temperament=2)
        result = {"events": []}
        self.engine._process_hot_spring(result)
        self.assertIn("明显不满", result["events"][0])
        self.assertEqual(npc.total_satisfaction, 50)
        self.assertEqual(self.engine.state.balance, 1000)
        self.assertEqual(self.engine.state.hot_spring_people_served_today, 19)
        self.assertNotIn("temperament", result["events"][0])

    def test_natural_overnight_and_reservation_guests_share_execution(self):
        for visit_type, source in (("overnight", "natural_overnight"), ("day", "reservation")):
            with self.subTest(visit_type=visit_type, source=source):
                self.engine.state.today_arrival_plan = []
                npc, action = self._add_guest(visit_type=visit_type, source=source)
                self.engine._process_hot_spring({"events": []})
                self.assertEqual(action["status"], "completed")
                self.assertEqual(npc.location, "hot_spring")

    def test_missing_arrival_npc_and_left_guest_are_skipped(self):
        _, missing_action = self._add_guest()
        self.engine.npc_pool.clear()
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(missing_action["result"], "missing_npc")

        npc, left_action = self._add_guest()
        npc.has_left = True
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(left_action["result"], "npc_left")

    def test_not_arrived_and_unavailable_are_skipped(self):
        npc, action = self._add_guest()
        self.engine.state.today_arrival_plan[0]["arrival_status"] = "pending"
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(action["result"], "not_arrived")

        npc, action = self._add_guest()
        self.engine.state.hot_spring_built = False
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(action["result"], "hot_spring_unavailable")

    def test_repeated_processing_is_idempotent(self):
        npc, action = self._add_guest(group_size=2)
        result = {"events": []}
        self.engine._process_hot_spring(result)
        state_after = (
            self.engine.state.balance,
            self.engine.state.today_income["hot_spring"],
            self.engine.state.hot_spring_people_served_today,
            npc.total_satisfaction,
        )
        self.engine._process_hot_spring(result)
        self.assertEqual(action["status"], "completed")
        self.assertEqual(state_after, (
            self.engine.state.balance,
            self.engine.state.today_income["hot_spring"],
            self.engine.state.hot_spring_people_served_today,
            npc.total_satisfaction,
        ))

    def test_turn5_hot_spring_runs_before_day_guest_departure(self):
        self.engine.state.turn = 5
        npc, action = self._add_guest(planned_turn=5)
        self.assertTrue(self.engine.submit_turn_plan([], [])['success'])
        self.engine.advance_turn()
        self.assertEqual(action["status"], "completed")
        self.assertTrue(npc.has_left)

    def test_new_day_resets_capacity_and_hot_spring_income(self):
        self.engine.state.hot_spring_people_served_today = 7
        self.engine.state.today_income["hot_spring"] = 560
        self.engine._new_day({"events": []})
        self.assertEqual(self.engine.state.hot_spring_people_served_today, 0)
        self.assertEqual(self.engine.state.today_income["hot_spring"], 0)

    def test_snapshot_restore_preserves_capacity_and_income(self):
        npc, action = self._add_guest(group_size=2)
        self.engine._process_hot_spring({"events": []})
        self.assertTrue(self.engine.save_state())
        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.hot_spring_people_served_today, 2)
        self.assertEqual(restored.state.today_income["hot_spring"], 160)

    def test_satisfaction_is_capped_at_one_hundred(self):
        npc, action = self._add_guest(group_size=1)
        npc.total_satisfaction = 98
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(npc.total_satisfaction, 100)

    def test_execution_error_rolls_back_all_mutations(self):
        npc, action = self._add_guest(group_size=2)
        balance = self.engine.state.balance
        income = self.engine.state.today_income["hot_spring"]
        people = self.engine.state.hot_spring_people_served_today
        location = npc.location
        satisfaction = npc.total_satisfaction

        class FailingEvents(list):
            def append(self, value):
                raise RuntimeError("event write failed")

        self.engine._process_hot_spring({"events": FailingEvents()})
        self.assertEqual(action["status"], "failed")
        self.assertEqual(action["result"], "execution_error")
        self.assertEqual(self.engine.state.balance, balance)
        self.assertEqual(self.engine.state.today_income["hot_spring"], income)
        self.assertEqual(self.engine.state.hot_spring_people_served_today, people)
        self.assertEqual(npc.location, location)
        self.assertEqual(npc.total_satisfaction, satisfaction)

    def test_overnight_guest_can_check_out_after_hot_spring_activity(self):
        npc = NPCGroup(
            id=self.engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            total_satisfaction=50,
        )
        self.engine._checkin_npc(npc, 1, {"events": []}, charge=False)
        npc.checkout_turn = 1
        tent = self.engine.tents[1]
        self.assertEqual(tent.occupied_by, npc.id)

        self.engine.state.turn = 5
        action = {
            "action": "hot_spring",
            "planned_turn": 5,
            "status": "pending",
        }
        self.engine.state.today_arrival_plan = [{
            "npc_id": npc.id,
            "planned_day": 1,
            "source": "natural_overnight",
            "visit_type": "overnight",
            "arrival_status": "arrived",
            "planned_actions": [action],
        }]
        self.engine._process_hot_spring({"events": []})
        self.assertEqual(npc.location, "hot_spring")

        self.engine.state.day = 2
        self.engine.state.turn = 1
        self.engine._process_checkout_partial({"events": []})

        self.assertTrue(npc.has_left)
        self.assertEqual(npc.location, "leaving")
        self.assertIsNone(tent.occupied_by)
        self.assertEqual(tent.status, "cleaning")

    def test_hot_spring_purchase_reload_and_activity_lifecycle(self):
        self.engine.state.hot_spring_built = False
        self.engine.state.turn = 6
        self.engine.state.balance = 8080
        self.engine.state.total_served_groups = 150
        for tent_id in range(2, 6):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.state.campsite_star = 4

        purchase = self.engine.purchase_growth_project("hot_spring")
        self.assertTrue(purchase["success"])
        self.assertEqual(purchase["balance_after"], 5080)
        self.assertTrue(self.engine.state.hot_spring_built)
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(restored.state.hot_spring_built)

        with mock.patch.object(
            restored,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 1},
        ), mock.patch.object(restored, "_generate_daily_reservation"), mock.patch.object(
            restored, "_roll_arrival_turn", return_value=2
        ), mock.patch.object(
            restored, "_build_dining_planned_action", return_value=None
        ), mock.patch.object(
            restored, "_build_paid_entertainment_planned_action", return_value=None
        ), mock.patch.object(
            restored, "_build_free_entertainment_planned_action", return_value=None
        ), mock.patch("game_engine.random.random", return_value=0.0), mock.patch(
            "game_engine.random.randint", return_value=1
        ), mock.patch(
            "game_engine.random.shuffle"
        ), mock.patch("game_engine.random.sample", return_value=[2]):
            restored._new_day({"events": []})

        entry = restored.state.today_arrival_plan[0]
        self.assertEqual(entry["source"], "natural_overnight")
        action = entry["planned_actions"][0]
        self.assertEqual(action["action"], "hot_spring")
        self.assertEqual(action["planned_turn"], 2)

        restored.state.turn = 2
        restored._process_planned_arrivals({"events": []})
        self.assertEqual(entry["arrival_status"], "arrived")
        npc = restored._find_npc(entry["npc_id"])
        self.assertIsNotNone(npc)
        satisfaction_before = npc.total_satisfaction
        restored._process_hot_spring({"events": []})

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["charged_amount"], 80)
        self.assertEqual(npc.total_satisfaction, satisfaction_before + 6)
        self.assertEqual(restored.state.today_income["hot_spring"], 80)
        self.assertEqual(restored.state.hot_spring_people_served_today, 1)

        restored._new_day({"events": []})
        self.assertTrue(restored.state.hot_spring_built)
        self.assertEqual(restored.state.hot_spring_people_served_today, 0)
        self.assertEqual(restored.state.today_income["hot_spring"], 0)


if __name__ == "__main__":
    unittest.main()
