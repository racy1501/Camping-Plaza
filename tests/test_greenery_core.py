"""绿化核心专项测试。"""

import os
import sys
import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup

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


class GreeneryCoreTests(unittest.TestCase):
    def test_new_game_greenery_defaults(self):
        engine = make_engine()
        greenery = engine.facilities["greenery"]

        self.assertEqual(greenery.level, 0)
        self.assertEqual(greenery.greenery_satisfaction, 2.0)
        self.assertEqual(greenery.greenery_decay_rate, 0.5)

    def test_lv0_and_lv1_decay_once_on_new_day_and_reset_processed_flag(self):
        for level, start_value, expected_value in (
            (0, 3.0, 2.5),
            (1, 6.0, 5.5),
        ):
            with self.subTest(level=level):
                engine = make_engine()
                greenery = engine.facilities["greenery"]
                greenery.level = level
                greenery.greenery_satisfaction = start_value
                engine.state.greenery_processed_today = False

                engine._new_day()

                self.assertEqual(greenery.greenery_satisfaction, expected_value)
                self.assertFalse(engine.state.greenery_processed_today)

    def test_greenery_decay_logs_event_when_value_really_drops(self):
        engine = make_engine()
        greenery = engine.facilities["greenery"]
        greenery.level = 0
        greenery.greenery_satisfaction = 3.0
        engine.state.greenery_processed_today = False
        result = {"events": []}

        engine._new_day(result)

        self.assertEqual(greenery.greenery_satisfaction, 2.5)
        self.assertIn("昨日未维护绿化，绿化值 3.0 → 2.5。", result["events"])

    def test_greenery_decay_does_not_log_when_maintained_lv2_or_zero(self):
        cases = (
            {"level": 0, "value": 3.0, "maintained_today": True},
            {"level": 2, "value": 9.0, "maintained_today": False},
            {"level": 0, "value": 0.0, "maintained_today": False},
        )

        for case in cases:
            with self.subTest(case=case):
                engine = make_engine()
                greenery = engine.facilities["greenery"]
                greenery.level = case["level"]
                greenery.greenery_satisfaction = case["value"]
                engine.state.greenery_processed_today = case["maintained_today"]
                result = {"events": []}

                engine._new_day(result)

                self.assertNotIn("昨日未维护绿化，绿化值", " ".join(result["events"]))

    def test_maintain_at_max_costs_50_caps_value_and_blocks_next_day_decay(self):
        engine = make_engine()
        greenery = engine.facilities["greenery"]
        engine.state.turn = 6
        engine.state.balance = 1000
        greenery.level = 0
        greenery.greenery_satisfaction = engine.GREENERY_LEVEL_MAX[0]

        message = engine.manage_greenery("maintain")

        self.assertIn("花费50金币", message)
        self.assertEqual(engine.state.balance, 950)
        self.assertEqual(greenery.greenery_satisfaction, 4.0)
        self.assertTrue(engine.state.greenery_processed_today)

        engine._new_day()

        self.assertEqual(greenery.greenery_satisfaction, 4.0)
        self.assertFalse(engine.state.greenery_processed_today)

    def test_insufficient_balance_does_not_maintain(self):
        engine = make_engine()
        greenery = engine.facilities["greenery"]
        engine.state.turn = 6
        engine.state.balance = 49
        greenery.greenery_satisfaction = 3.0

        message = engine.manage_greenery("maintain")

        self.assertIn("余额不足", message)
        self.assertEqual(engine.state.balance, 49)
        self.assertEqual(greenery.greenery_satisfaction, 3.0)
        self.assertFalse(engine.state.greenery_processed_today)

    def test_greenery_upgrade_adds_two_once_and_marks_processed_today(self):
        engine = make_engine()
        greenery = engine.facilities["greenery"]
        engine.state.turn = 6
        engine.state.balance = 1000
        greenery.level = 0
        greenery.greenery_satisfaction = 4.0

        result = engine.upgrade_facility("greenery")

        self.assertTrue(result["success"])
        self.assertEqual(greenery.level, 1)
        self.assertEqual(greenery.greenery_satisfaction, 6.0)
        self.assertTrue(engine.state.greenery_processed_today)

    def test_lv2_does_not_decay_and_can_still_maintain_when_not_full(self):
        engine = make_engine()
        greenery = engine.facilities["greenery"]
        greenery.level = 2
        greenery.greenery_satisfaction = 9.0
        greenery.greenery_decay_rate = 0
        engine.state.greenery_processed_today = False

        engine._new_day()

        self.assertEqual(greenery.greenery_satisfaction, 9.0)
        self.assertFalse(engine.state.greenery_processed_today)

        engine.state.turn = 6
        engine.state.balance = 1000
        message = engine.manage_greenery("maintain")

        self.assertIn("花费50金币", message)
        self.assertEqual(engine.state.balance, 950)
        self.assertEqual(greenery.greenery_satisfaction, 10.0)
        self.assertTrue(engine.state.greenery_processed_today)

    def test_get_full_state_greenery_summary_for_lv0_not_maintained(self):
        engine = make_engine()

        greenery = engine.get_full_state()["greenery"]

        self.assertEqual(greenery["level"], 0)
        self.assertEqual(greenery["value"], 2.0)
        self.assertEqual(greenery["max"], 4.0)
        self.assertFalse(greenery["maintained_today"])
        self.assertEqual(greenery["decay_next_day"], 0.5)

    def test_get_full_state_greenery_summary_has_zero_decay_when_maintained_or_lv2(self):
        engine = make_engine()
        engine.state.greenery_processed_today = True
        maintained_greenery = engine.get_full_state()["greenery"]

        self.assertEqual(maintained_greenery["max"], 4.0)
        self.assertTrue(maintained_greenery["maintained_today"])
        self.assertEqual(maintained_greenery["decay_next_day"], 0.0)

        engine = make_engine()
        engine.facilities["greenery"].level = 2
        engine.facilities["greenery"].greenery_satisfaction = 9.0
        engine.state.greenery_processed_today = False
        lv2_greenery = engine.get_full_state()["greenery"]

        self.assertEqual(lv2_greenery["level"], 2)
        self.assertEqual(lv2_greenery["value"], 9.0)
        self.assertEqual(lv2_greenery["max"], 10.0)
        self.assertFalse(lv2_greenery["maintained_today"])
        self.assertEqual(lv2_greenery["decay_next_day"], 0.0)

    def test_day_guest_arrival_gets_greenery_bonus_once(self):
        engine = make_engine()
        engine.state.turn = 2
        engine.state.today_arrival_plan_day = engine.state.day
        engine.facilities["greenery"].greenery_satisfaction = 2.5
        entry = {
            "npc_id": engine._next_npc_id(),
            "group_size": 2,
            "visit_type": "day",
            "arrival_turn": 2,
            "arrival_status": "pending",
            "planned_day": engine.state.day,
            "source": "natural_day",
            "total_satisfaction": 60.0,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        engine.state.today_arrival_plan = [entry]

        engine._process_planned_arrivals({"events": []})

        guest = engine.npc_pool[0]
        self.assertEqual(guest.total_satisfaction, 62.5)
        self.assertTrue(guest.greenery_entry_bonus_applied)

    def test_overnight_guest_checkin_gets_greenery_bonus_once(self):
        engine = make_engine()
        engine.facilities["greenery"].greenery_satisfaction = 2.5
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="overnight",
            total_satisfaction=60.0,
        )

        engine._checkin_npc(guest, 1, {"events": []}, charge=False)

        self.assertEqual(guest.total_satisfaction, 72.5)
        self.assertTrue(guest.greenery_entry_bonus_applied)

    def test_same_group_does_not_get_greenery_bonus_twice(self):
        engine = make_engine()
        engine.facilities["greenery"].greenery_satisfaction = 2.5
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            total_satisfaction=60.0,
        )

        engine._apply_greenery_entry_bonus_once(guest)
        first_total = guest.total_satisfaction
        engine._apply_greenery_entry_bonus_once(guest)

        self.assertEqual(first_total, 62.5)
        self.assertEqual(guest.total_satisfaction, 62.5)
        self.assertTrue(guest.greenery_entry_bonus_applied)

    def test_greenery_value_2_point_5_adds_exactly_2_point_5(self):
        engine = make_engine()
        engine.facilities["greenery"].greenery_satisfaction = 2.5
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            total_satisfaction=60.0,
        )

        engine._apply_greenery_entry_bonus_once(guest)

        self.assertEqual(guest.total_satisfaction, 62.5)

    def test_checkout_does_not_add_greenery_bonus(self):
        engine = make_engine()
        engine.facilities["greenery"].greenery_satisfaction = 2.5
        tent = engine.tents[1]
        tent.status = "occupied"
        tent.occupied_by = 1
        guest = NPCGroup(
            id=1,
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            total_satisfaction=60.0,
        )
        engine.npc_pool.append(guest)

        engine._checkout_npc(guest, {"events": []})

        self.assertEqual(guest.total_satisfaction, 60.0)
        self.assertEqual(guest.location, "leaving")
        self.assertTrue(guest.has_left)

    def test_legacy_save_without_greenery_bonus_field_loads(self):
        base_engine = make_engine()
        guest = NPCGroup(
            id=base_engine._next_npc_id(),
            group_size=2,
            visit_type="overnight",
            location="tent_1",
            total_satisfaction=60.0,
        )
        guest_payload = asdict(guest)
        guest_payload.pop("greenery_entry_bonus_applied", None)
        payload = {
            "snapshot_version": CampingPlazaEngine.SNAPSHOT_VERSION,
            "state": asdict(base_engine.state),
            "tents": {str(tid): asdict(tent) for tid, tent in base_engine.tents.items()},
            "facilities": {
                name: asdict(facility) for name, facility in base_engine.facilities.items()
            },
            "npc_pool": [guest_payload],
            "npc_history": [],
            "npc_id_counter": base_engine._npc_id_counter,
        }

        fake_conn = mock.Mock()
        fake_conn.execute.return_value.fetchone.return_value = (
            json.dumps(payload, ensure_ascii=False),
        )

        with mock.patch("game_engine.os.path.exists", return_value=True):
            with mock.patch("game_engine.sqlite3.connect", return_value=fake_conn):
                with mock.patch.object(
                    CampingPlazaEngine, "_ensure_today_arrival_plan", return_value=False
                ):
                    loaded = CampingPlazaEngine(db_path="legacy_snapshot.db")

        self.assertEqual(len(loaded.npc_pool), 1)
        self.assertFalse(loaded.npc_pool[0].greenery_entry_bonus_applied)


if __name__ == "__main__":
    unittest.main()
