import json
import os
import sqlite3
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class GrowthProgressTests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir(),
            "camping_plaza_fix_temp",
        )
        os.makedirs(self._db_dir, exist_ok=True)
        self._db_paths = []
        self.db_path = self._new_db_path("growth")
        self.engine = CampingPlazaEngine(db_path=self.db_path)

    def tearDown(self):
        for db_path in self._db_paths:
            try:
                os.remove(db_path)
            except FileNotFoundError:
                pass

    def _new_db_path(self, name):
        db_path = os.path.join(self._db_dir, f"growth_progress_{name}.sqlite")
        try:
            os.remove(db_path)
        except FileNotFoundError:
            pass
        self._db_paths.append(db_path)
        return db_path

    def _arrival_entry(
        self, npc_id, visit_type, source, *, group_size=2, tent_id=None, paid=False
    ):
        return {
            "npc_id": npc_id,
            "group_size": group_size,
            "visit_type": visit_type,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "total_satisfaction": 60,
            "arrival_turn": 2,
            "planned_day": self.engine.state.day,
            "source": source,
            "arrival_status": "pending",
            "planned_actions": [],
            "is_reserved": source == "reservation",
            "paid": paid,
            "tent_id": tent_id,
            "day_to_overnight_intent": False,
        }

    def _set_single_arrival(self, entry):
        self.engine.state.turn = 2
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [entry]

    def _attach_dining_action(self, npc, *, status="pending"):
        self.engine.state.turn = 2
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        action = {
            "action": "dining",
            "menu_key": "basic",
            "planned_turn": 2,
            "status": status,
        }
        self.engine.state.today_arrival_plan = [{
            "npc_id": npc.id,
            "planned_day": self.engine.state.day,
            "arrival_status": "arrived",
            "planned_actions": [action],
        }]
        return action

    def test_new_game_starts_with_zero_growth_counters(self):
        progress = self.engine.get_growth_progress()

        self.assertEqual(progress["total_served_groups"], 0)
        self.assertEqual(progress["successful_dining_groups"], 0)
        self.assertEqual(progress["successful_paid_entertainment_groups"], 0)
        self.assertEqual(progress["successful_greenery_maintenance_count"], 0)

    def test_growth_counters_survive_snapshot_restore(self):
        self.engine.state.total_served_groups = 4
        self.engine.state.successful_dining_groups = 3
        self.engine.state.successful_paid_entertainment_groups = 2
        self.engine.state.successful_greenery_maintenance_count = 1
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.get_growth_progress(), self.engine.get_growth_progress())

    def test_old_snapshot_without_growth_counters_loads_defaults(self):
        npc = NPCGroup(
            id=1,
            group_size=2,
            visit_type="day",
            growth_served_recorded=True,
            growth_dining_recorded=True,
            growth_paid_entertainment_recorded=True,
        )
        self.engine.npc_pool.append(npc)
        self.assertTrue(self.engine.save_state())
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE id = 1"
            ).fetchone()[0]
            payload = json.loads(raw)
            for key in (
                "total_served_groups",
                "successful_dining_groups",
                "successful_paid_entertainment_groups",
                "successful_greenery_maintenance_count",
            ):
                payload["state"].pop(key, None)
            for npc_data in payload["npc_pool"]:
                for key in (
                    "growth_served_recorded",
                    "growth_dining_recorded",
                    "growth_paid_entertainment_recorded",
                ):
                    npc_data.pop(key, None)
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ? WHERE id = 1",
                (json.dumps(payload, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()

        restored = CampingPlazaEngine(db_path=self.db_path)
        progress = restored.get_growth_progress()
        self.assertEqual(progress["total_served_groups"], 0)
        self.assertEqual(progress["successful_dining_groups"], 0)
        self.assertEqual(progress["successful_paid_entertainment_groups"], 0)
        self.assertEqual(progress["successful_greenery_maintenance_count"], 0)
        self.assertEqual(len(restored.npc_pool), 1)
        self.assertFalse(restored.npc_pool[0].growth_served_recorded)
        self.assertFalse(restored.npc_pool[0].growth_dining_recorded)
        self.assertFalse(restored.npc_pool[0].growth_paid_entertainment_recorded)

    def test_day_natural_and_reservation_arrivals_count_once(self):
        for source, paid in (("natural_day", False), ("reservation", True)):
            with self.subTest(source=source):
                engine = CampingPlazaEngine(
                    db_path=self._new_db_path(source)
                )
                self.engine = engine
                entry = self._arrival_entry(10, "day", source, paid=paid)
                self._set_single_arrival(entry)

                engine._process_planned_arrivals({"events": []})
                engine._process_planned_arrivals({"events": []})

                self.assertEqual(engine.state.total_served_groups, 1)

    def test_overnight_natural_and_reservation_arrivals_count_once(self):
        for source, paid in (("natural_overnight", False), ("reservation", True)):
            with self.subTest(source=source):
                engine = CampingPlazaEngine(
                    db_path=self._new_db_path(source)
                )
                self.engine = engine
                entry = self._arrival_entry(
                    20, "overnight", source, tent_id=1, paid=paid
                )
                self._set_single_arrival(entry)

                engine._process_planned_arrivals({"events": []})
                engine._process_planned_arrivals({"events": []})

                self.assertEqual(engine.state.total_served_groups, 1)

    def test_rejected_or_merely_locked_reservations_do_not_count(self):
        entry = self._arrival_entry(30, "overnight", "natural_overnight", group_size=3)
        self._set_single_arrival(entry)
        self.engine._process_planned_arrivals({"events": []})
        self.assertEqual(entry["arrival_status"], "turned_away_full")
        self.assertEqual(self.engine.state.total_served_groups, 0)

        self.engine.state.reservation = {"group_size": 2, "status": "accepted"}
        self.engine.state.reserved_tent_id = 1
        self.engine.state.reserved_tent_day = self.engine.state.day + 1
        self.assertEqual(self.engine.state.total_served_groups, 0)

    def test_day_to_overnight_does_not_count_served_group_again(self):
        guest = NPCGroup(id=40, group_size=2, visit_type="day", location="campsite")
        self.engine.npc_pool.append(guest)
        self.engine._record_served_group_once(guest)
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [{
            "npc_id": guest.id,
            "planned_day": self.engine.state.day,
            "visit_type": "day",
            "day_to_overnight_intent": True,
        }]

        self.engine._process_day_to_overnight({"events": []})

        self.assertEqual(self.engine.state.total_served_groups, 1)
        self.assertEqual(guest.visit_type, "overnight")

    def test_normal_and_restock_dining_count_once_each(self):
        normal = NPCGroup(id=50, group_size=2, visit_type="day")
        self.engine.npc_pool.append(normal)
        normal_action = self._attach_dining_action(normal)
        self.engine.state.food_stock = 2
        self.engine._process_dining({"events": []})
        self.engine._process_dining({"events": []})
        self.assertEqual(normal_action["status"], "completed")
        self.assertEqual(self.engine.state.successful_dining_groups, 1)

        rescue = NPCGroup(id=51, group_size=2, visit_type="day")
        self.engine.npc_pool = [rescue]
        rescue_action = self._attach_dining_action(rescue)
        self.engine.state.food_stock = 0
        self.engine._process_dining({"events": []})
        self.assertEqual(rescue_action["status"], "waiting_for_restock")
        self.assertEqual(self.engine.state.successful_dining_groups, 1)

        self.engine.state.food_stock = 2
        self.engine._retry_waiting_dining_after_restock({"events": []})
        self.engine._retry_waiting_dining_after_restock({"events": []})
        self.assertEqual(rescue_action["status"], "completed")
        self.assertEqual(self.engine.state.successful_dining_groups, 2)

    def test_paid_entertainment_counts_but_free_and_invalid_do_not(self):
        npc = NPCGroup(id=60, group_size=2, visit_type="day")
        self.engine.npc_pool.append(npc)
        self.engine.state.turn = 2
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [{
            "npc_id": npc.id,
            "planned_day": self.engine.state.day,
            "arrival_status": "arrived",
            "planned_actions": [
                {"action": "paid_entertainment", "tier_key": "basic", "planned_turn": 2, "status": "pending"},
                {"action": "free_entertainment", "planned_turn": 2, "status": "pending"},
            ],
        }]
        self.engine._process_entertainment({"events": []})
        self.assertEqual(self.engine.state.successful_paid_entertainment_groups, 1)

        invalid = NPCGroup(id=61, group_size=2, visit_type="day")
        self.engine.npc_pool = [invalid]
        self.engine.state.today_arrival_plan = [{
            "npc_id": invalid.id,
            "planned_day": self.engine.state.day,
            "arrival_status": "arrived",
            "planned_actions": [{
                "action": "paid_entertainment",
                "tier_key": "missing",
                "planned_turn": 2,
                "status": "pending",
            }],
        }]
        self.engine._process_entertainment({"events": []})
        self.assertEqual(self.engine.state.successful_paid_entertainment_groups, 1)

    def test_greenery_maintenance_counts_only_after_success(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 100
        self.engine.manage_greenery("maintain")
        self.engine.manage_greenery("maintain")
        self.assertEqual(self.engine.state.successful_greenery_maintenance_count, 1)

        insufficient = CampingPlazaEngine(db_path=self._new_db_path("low"))
        insufficient.state.turn = 6
        insufficient.state.balance = 49
        insufficient.manage_greenery("maintain")
        self.assertEqual(insufficient.state.successful_greenery_maintenance_count, 0)

        wrong_turn = CampingPlazaEngine(db_path=self._new_db_path("turn"))
        wrong_turn.state.turn = 1
        wrong_turn.manage_greenery("maintain")
        self.assertEqual(wrong_turn.state.successful_greenery_maintenance_count, 0)

    def test_growth_nodes_are_derived_and_progress_query_is_read_only(self):
        for tent_id in range(2, 7):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.facilities["greenery"].level = 2
        before = {
            "balance": self.engine.state.balance,
            "food_stock": self.engine.state.food_stock,
            "tent_state": [
                (tent.is_unlocked, tent.status)
                for tent in self.engine.tents.values()
            ],
            "facility_levels": {
                name: facility.level for name, facility in self.engine.facilities.items()
            },
            "counters": (
                self.engine.state.total_served_groups,
                self.engine.state.successful_dining_groups,
                self.engine.state.successful_paid_entertainment_groups,
                self.engine.state.successful_greenery_maintenance_count,
            ),
        }

        progress = self.engine.get_growth_progress()

        self.assertEqual(progress["unlocked_tent_nodes"], 5)
        self.assertEqual(progress["dining_nodes"], 2)
        self.assertEqual(progress["entertainment_nodes"], 2)
        self.assertEqual(progress["greenery_nodes"], 2)
        self.assertEqual(progress["completed_growth_nodes"], 11)
        self.assertGreaterEqual(progress["completed_growth_nodes"], 0)
        self.assertLessEqual(progress["completed_growth_nodes"], 11)
        self.assertEqual(before["balance"], self.engine.state.balance)
        self.assertEqual(before["food_stock"], self.engine.state.food_stock)
        self.assertEqual(
            before["tent_state"],
            [(tent.is_unlocked, tent.status) for tent in self.engine.tents.values()],
        )
        self.assertEqual(
            before["facility_levels"],
            {name: facility.level for name, facility in self.engine.facilities.items()},
        )
        self.assertEqual(
            before["counters"],
            (
                self.engine.state.total_served_groups,
                self.engine.state.successful_dining_groups,
                self.engine.state.successful_paid_entertainment_groups,
                self.engine.state.successful_greenery_maintenance_count,
            ),
        )

    def test_invalid_facility_level_raises_without_correcting_state(self):
        self.engine.facilities["dining"].level = 3
        before = {
            "balance": self.engine.state.balance,
            "food_stock": self.engine.state.food_stock,
            "dining_level": self.engine.facilities["dining"].level,
            "counters": (
                self.engine.state.total_served_groups,
                self.engine.state.successful_dining_groups,
                self.engine.state.successful_paid_entertainment_groups,
                self.engine.state.successful_greenery_maintenance_count,
            ),
        }

        with self.assertRaisesRegex(
            ValueError, r"invalid growth facility level: dining=3"
        ):
            self.engine.get_growth_progress()

        self.assertEqual(self.engine.state.balance, before["balance"])
        self.assertEqual(self.engine.state.food_stock, before["food_stock"])
        self.assertEqual(self.engine.facilities["dining"].level, before["dining_level"])
        self.assertEqual(
            (
                self.engine.state.total_served_groups,
                self.engine.state.successful_dining_groups,
                self.engine.state.successful_paid_entertainment_groups,
                self.engine.state.successful_greenery_maintenance_count,
            ),
            before["counters"],
        )
