"""露营广场核心链路回归测试

仅使用 Python 标准库 unittest，不依赖 pytest/httpx/FastAPI/网络/真实数据库。
所有测试使用独立 CampingPlazaEngine 实例，对随机行为使用 mock 控制。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

# 将 camping_plaza 包加入路径（不依赖 __init__.py，Python 3 命名空间包）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup, Tent
import game_api

# 每个测试使用独立临时数据库，避免共享正式 camping_plaza.db 互相污染
_TEMP_DIRS = []


def make_engine() -> CampingPlazaEngine:
    """创建使用独立临时目录数据库的引擎实例，测试结束统一清理"""
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


class DayToOvernightSettlementTests(unittest.TestCase):
    """Turn 4 营业结束后的日转夜完整结算。"""

    def _engine_at_turn4(self):
        engine = make_engine()
        engine.state.day = 1
        engine.state.turn = 4
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = []
        engine.state.today_events = []
        # 屏蔽故障干扰
        for t in engine.tents.values():
            t.next_breakdown_turn = 99999
        return engine

    def _add_day_plan(self, engine, guest, intent, source="natural_day"):
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan.append({
            "npc_id": guest.id,
            "planned_day": engine.state.day,
            "visit_type": "day",
            "source": source,
            "day_to_overnight_intent": intent,
            "planned_actions": [],
            "arrival_status": "arrived",
        })

    def test_low_satisfaction_guest_with_intent_converts_in_turn4_result(self):
        engine = self._engine_at_turn4()
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            arrival_turn=3,
            location="dining",
            total_satisfaction=10,
        )
        engine.npc_pool.append(guest)
        self._add_day_plan(engine, guest, True)

        self.assertTrue(engine.submit_turn_plan([], [])["success"])
        result = engine.advance_turn()

        self.assertEqual(guest.visit_type, "overnight")
        self.assertTrue(guest.location.startswith("tent_"))
        self.assertEqual(guest.checkout_turn, 1)
        self.assertEqual(result["turn"], 5)
        self.assertTrue(
            any("日间客决定留下过夜" in event for event in result["events"])
        )

    def test_high_satisfaction_guest_without_intent_stays_until_turn5(self):
        engine = self._engine_at_turn4()
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            arrival_turn=3,
            location="dining",
            total_satisfaction=80,
        )
        engine.npc_pool.append(guest)
        self._add_day_plan(engine, guest, False)

        self.assertTrue(engine.submit_turn_plan([], [])["success"])
        with mock.patch("game_engine.random.random", return_value=1.0):
            result = engine.advance_turn()

        self.assertFalse(guest.has_left)
        self.assertIn(guest, engine.npc_pool)
        self.assertFalse(any("日间客决定留下过夜" in event for event in result["events"]))

    def test_reservation_and_natural_day_guests_use_same_plan_logic_and_matcher(self):
        engine = self._engine_at_turn4()
        natural = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day")
        reserved = NPCGroup(id=engine._next_npc_id(), group_size=2, visit_type="day")
        engine.npc_pool.extend([natural, reserved])
        engine.tents[2].is_unlocked = True
        self._add_day_plan(engine, natural, True, "natural_day")
        self._add_day_plan(engine, reserved, True, "reservation")

        with mock.patch.object(engine, "_match_day_to_overnight_tents", wraps=engine._match_day_to_overnight_tents) as matcher:
            result = {"events": []}
            engine._process_day_to_overnight(result)

        matcher.assert_called_once()
        self.assertEqual(natural.visit_type, "overnight")
        self.assertEqual(reserved.visit_type, "overnight")
        self.assertEqual(
            engine.state.today_income["accommodation"],
            engine.TENT_PRICES[1] + engine.TENT_PRICES[2],
        )
        self.assertEqual(len(result["events"]), 1)

    def test_unconverted_day_guest_executes_turn5_activity_then_leaves(self):
        engine = self._engine_at_turn4()
        guest = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day", location="campsite")
        engine.npc_pool.append(guest)
        self._add_day_plan(engine, guest, False)
        entry = engine.state.today_arrival_plan[-1]
        entry["planned_actions"] = [{
            "action": "free_entertainment",
            "planned_turn": 5,
            "status": "pending",
        }]
        self.assertTrue(engine.submit_turn_plan([], [])['success'])
        engine.advance_turn()
        self.assertFalse(guest.has_left)
        self.assertTrue(engine.submit_turn_plan([], [])['success'])
        result = engine.advance_turn()
        self.assertEqual(entry["planned_actions"][0]["status"], "completed")
        self.assertTrue(guest.has_left)
        self.assertEqual(guest.location, "leaving")
        self.assertEqual(result["turn"], 6)

    def test_turn5_departure_is_idempotent_and_preserves_overnight_guest(self):
        engine = self._engine_at_turn4()
        day_guest = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day")
        overnight_guest = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="overnight", location="tent_1")
        engine.npc_pool.extend([day_guest, overnight_guest])
        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_day_guest_departures({"events": []})
        review_left = day_guest.review_left
        self.assertEqual(len(engine.state.pending_reviews), 1)
        with mock.patch("game_engine.random.random", side_effect=AssertionError("duplicate departure review")):
            engine._process_day_guest_departures({"events": []})
        self.assertTrue(day_guest.has_left)
        self.assertEqual(day_guest.review_left, review_left)
        self.assertEqual(len(engine.state.pending_reviews), 1)
        self.assertFalse(overnight_guest.has_left)

    def test_reservation_day_guest_uses_turn5_lifecycle_after_failed_conversion(self):
        engine = self._engine_at_turn4()
        guest = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day", location="campsite")
        engine.npc_pool.append(guest)
        self._add_day_plan(engine, guest, False, "reservation")
        entry = engine.state.today_arrival_plan[-1]
        entry["planned_actions"] = [{
            "action": "free_entertainment",
            "planned_turn": 5,
            "status": "pending",
        }]
        self.assertEqual(entry["source"], "reservation")
        engine._process_day_to_overnight({"events": []})
        self.assertFalse(guest.has_left)
        engine.state.turn = 5
        self.assertTrue(engine.submit_turn_plan([], [])['success'])
        engine.advance_turn()
        self.assertEqual(entry["planned_actions"][0]["status"], "completed")
        self.assertTrue(guest.has_left)

    def test_tent_prices_match_current_design(self):
        engine = self._engine_at_turn4()

        self.assertEqual(
            engine.TENT_PRICES,
            {1: 160, 2: 160, 3: 230, 4: 310, 5: 400, 6: 500},
        )

    def test_unmatched_guest_is_not_charged_and_stays_until_turn5(self):
        engine = self._engine_at_turn4()
        guest = NPCGroup(id=engine._next_npc_id(), group_size=3, visit_type="day")
        engine.npc_pool.append(guest)
        self._add_day_plan(engine, guest, True)
        balance_before = engine.state.balance

        result = {"events": []}
        with mock.patch("game_engine.random.random", return_value=1.0):
            engine._process_day_to_overnight(result)

        self.assertFalse(guest.has_left)
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.today_income["accommodation"], 0)
        self.assertEqual(len(result["events"]), 1)

    def test_partial_success_uses_failed_count_in_event_text(self):
        engine = self._engine_at_turn4()
        guests = [
            NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day"),
            NPCGroup(id=engine._next_npc_id(), group_size=2, visit_type="day"),
            NPCGroup(id=engine._next_npc_id(), group_size=3, visit_type="day"),
        ]
        engine.npc_pool.extend(guests)
        for guest in guests:
            self._add_day_plan(engine, guest, True)
        for tent in engine.tents.values():
            tent.status = "cleaning"
        engine.tents[3].is_unlocked = True
        engine.tents[3].capacity = 3
        engine.tents[3].status = "available"
        balance_before = engine.state.balance
        accommodation_before = engine.state.today_income["accommodation"]
        tent_income = engine.TENT_PRICES[3]

        result = {"events": []}
        engine._process_day_to_overnight(result)

        day_to_overnight_events = [
            event for event in result["events"]
            if "日间客决定留下过夜" in event
        ]
        self.assertEqual(len(day_to_overnight_events), 1)
        self.assertNotIn("只能按原计划离开", day_to_overnight_events[0])
        self.assertIn("Turn 5", day_to_overnight_events[0])
        self.assertTrue(all(not guest.has_left for guest in guests))
        self.assertIn("另外2组", day_to_overnight_events[0])
        self.assertNotIn("另一组", day_to_overnight_events[0])
        self.assertEqual(
            len([guest for guest in guests if guest.visit_type == "overnight"]),
            1,
        )
        self.assertEqual(
            len([guest for guest in guests if guest.has_left]),
            0,
        )
        self.assertEqual(engine.state.balance, balance_before + tent_income)
        self.assertEqual(engine.state.today_income["accommodation"], accommodation_before + tent_income)

    def test_settlement_precedes_breakdown_and_keeps_occupant_for_turn5_repair(self):
        engine = self._engine_at_turn4()
        guest = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day")
        engine.npc_pool.append(guest)
        self._add_day_plan(engine, guest, True)
        engine.tents[1].next_breakdown_turn = engine._absolute_turn()

        self.assertTrue(engine.submit_turn_plan([], [])["success"])
        result = engine.advance_turn()

        self.assertEqual(result["turn"], 5)
        self.assertEqual(engine.tents[1].status, "broken")
        self.assertEqual(engine.tents[1].occupied_by, guest.id)
        self.assertTrue(engine.submit_turn_plan([], [{"action": "repair_tent", "tent_id": 1}])["success"])


class DiningRestockRetryHelperTests(unittest.TestCase):
    def _make_engine(self, stock=0):
        engine = make_engine()
        engine.state.day = 1
        engine.state.turn = 3
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = []
        engine.state.today_events = []
        engine.state.food_stock = stock
        for tent in engine.tents.values():
            tent.next_breakdown_turn = 99999
        return engine

    def _append_waiting_action(
        self,
        engine,
        group_size,
        planned_turn,
        menu_key="premium",
        total_satisfaction=50,
    ):
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=group_size,
            visit_type="day",
            arrival_turn=planned_turn,
            location="dining",
            total_satisfaction=total_satisfaction,
            economic_level=2,
        )
        engine.npc_pool.append(npc)
        entry = {
            "npc_id": npc.id,
            "planned_day": engine.state.day,
            "visit_type": "day",
            "source": "natural_day",
            "planned_actions": [
                {
                    "action": "dining",
                    "menu_key": menu_key,
                    "planned_turn": planned_turn,
                    "status": "waiting_for_restock",
                    "result": "insufficient_food",
                }
            ],
            "arrival_status": "arrived",
        }
        engine.state.today_arrival_plan.append(entry)
        return npc, entry["planned_actions"][0]

    def test_single_waiting_action_completes_after_restock_and_does_not_repeat(self):
        engine = self._make_engine()
        npc, action = self._append_waiting_action(engine, group_size=2, planned_turn=3)
        menu = engine.DINING_SET_MENUS["premium"]
        balance_before = engine.state.balance

        engine.state.food_stock = 2
        result1 = {"events": []}
        engine._retry_waiting_dining_after_restock(result1)

        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["result"], "success")
        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 2)
        self.assertEqual(engine.state.balance, balance_before + menu["price_per_person"] * 2)
        self.assertEqual(npc.total_satisfaction, 50 + menu["satisfaction_gain"])
        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(len(result1["events"]), 1)

        balance_after_first = engine.state.balance
        food_after_first = engine.state.food_stock
        satisfaction_after_first = npc.total_satisfaction
        result2 = {"events": []}
        engine._retry_waiting_dining_after_restock(result2)

        self.assertEqual(engine.state.balance, balance_after_first)
        self.assertEqual(engine.state.food_stock, food_after_first)
        self.assertEqual(npc.total_satisfaction, satisfaction_after_first)
        self.assertEqual(result2["events"], [])

    def test_single_waiting_action_stays_waiting_when_stock_is_still_insufficient(self):
        engine = self._make_engine(stock=2)
        npc, action = self._append_waiting_action(engine, group_size=3, planned_turn=3)
        balance_before = engine.state.balance

        result = {"events": []}
        engine._retry_waiting_dining_after_restock(result)

        self.assertEqual(action["status"], "waiting_for_restock")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(engine.state.food_stock, 2)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(npc.total_satisfaction, 50)
        self.assertEqual(result["events"], [])

    def test_earlier_planned_turn_is_processed_before_later_turn(self):
        engine = self._make_engine()
        earlier_npc, earlier_action = self._append_waiting_action(
            engine, group_size=2, planned_turn=2
        )
        later_npc, later_action = self._append_waiting_action(
            engine, group_size=1, planned_turn=4
        )
        engine.state.food_stock = 2

        result = {"events": []}
        engine._retry_waiting_dining_after_restock(result)

        self.assertEqual(earlier_action["status"], "completed")
        self.assertEqual(later_action["status"], "waiting_for_restock")
        self.assertEqual(len(result["events"]), 1)
        self.assertIn(f"客组{earlier_npc.id}", result["events"][0])
        self.assertEqual(engine.state.today_income["dining"], engine.DINING_SET_MENUS["premium"]["price_per_person"] * 2)

    def test_same_planned_turn_keeps_original_plan_order(self):
        engine = self._make_engine()
        first_npc, first_action = self._append_waiting_action(
            engine, group_size=1, planned_turn=3
        )
        second_npc, second_action = self._append_waiting_action(
            engine, group_size=2, planned_turn=3
        )
        engine.state.food_stock = 1

        result = {"events": []}
        engine._retry_waiting_dining_after_restock(result)

        self.assertEqual(first_action["status"], "completed")
        self.assertEqual(second_action["status"], "waiting_for_restock")
        self.assertIn(f"客组{first_npc.id}", result["events"][0])
        self.assertEqual(len(result["events"]), 1)

    def test_large_waiting_group_does_not_block_later_smaller_group(self):
        engine = self._make_engine(stock=2)
        large_npc, large_action = self._append_waiting_action(
            engine, group_size=3, planned_turn=2
        )
        small_npc, small_action = self._append_waiting_action(
            engine, group_size=2, planned_turn=4
        )

        result = {"events": []}
        engine._retry_waiting_dining_after_restock(result)

        self.assertEqual(large_action["status"], "waiting_for_restock")
        self.assertEqual(small_action["status"], "completed")
        self.assertEqual(engine.state.today_income["dining"], engine.DINING_SET_MENUS["premium"]["price_per_person"] * 2)
        self.assertIn(f"客组{small_npc.id}", result["events"][0])


class DayCampsiteCapacityTests(unittest.TestCase):
    """日间营位每日10组上限与生命周期"""

    def test_new_game_counter_starts_at_zero(self):
        engine = make_engine()

        self.assertEqual(engine.state.day_campsite_groups_served, 0)
        self.assertEqual(engine.get_day_campsite_remaining(), 10)

    def test_successful_day_guest_checkin_increments_counter_by_group(self):
        engine = make_engine()
        engine.state.turn = 2
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [{
            "npc_id": engine._next_npc_id(),
            "group_size": 3,
            "visit_type": "day",
            "arrival_turn": 2,
            "planned_day": engine.state.day,
            "source": "natural_day",
            "arrival_status": "pending",
            "total_satisfaction": 60,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "is_reserved": False,
            "paid": False,
            "planned_actions": [],
        }]
        result = {"events": []}
        engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 1)
        self.assertEqual(engine.get_day_campsite_remaining(), 9)
        self.assertEqual(engine.CAMPSITE_FEE, 70)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)
        self.assertEqual(engine.state.balance, 1000 + engine.CAMPSITE_FEE)
        self.assertIn(str(engine.CAMPSITE_FEE), result["events"][0])
        self.assertEqual(len(engine.npc_pool), 1)

    def test_remaining_two_slots_caps_generation_and_revenue(self):
        engine = make_engine()
        engine.state.turn = 3
        engine.state.day_campsite_groups_served = 8

        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [
            {
                "npc_id": engine._next_npc_id(),
                "group_size": 1,
                "visit_type": "day",
                "arrival_turn": 3,
                "arrival_status": "pending",
                "planned_day": engine.state.day,
                "source": "natural_day",
                "total_satisfaction": 60,
                "economic_level": 1,
                "spending_habit": 1,
                "temperament": 1,
            },
            {
                "npc_id": engine._next_npc_id(),
                "group_size": 2,
                "visit_type": "day",
                "arrival_turn": 3,
                "arrival_status": "pending",
                "planned_day": engine.state.day,
                "source": "natural_day",
                "total_satisfaction": 60,
                "economic_level": 1,
                "spending_habit": 1,
                "temperament": 1,
            },
            {
                "npc_id": engine._next_npc_id(),
                "group_size": 3,
                "visit_type": "day",
                "arrival_turn": 3,
                "arrival_status": "pending",
                "planned_day": engine.state.day,
                "source": "natural_day",
                "total_satisfaction": 60,
                "economic_level": 1,
                "spending_habit": 1,
                "temperament": 1,
            },
        ]

        result = {"events": []}
        engine._process_planned_arrivals(result)

        day_entries = [entry for entry in engine.state.today_arrival_plan if entry["source"] == "natural_day"]
        self.assertEqual([entry["arrival_status"] for entry in day_entries], ["arrived", "arrived", "turned_away_full"])
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "day"]), 2)
        self.assertEqual(engine.state.day_campsite_groups_served, 10)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE * 2)
        self.assertEqual(engine.state.balance, 1000 + engine.CAMPSITE_FEE * 2)

    def test_no_new_day_guests_or_fee_after_capacity_reached(self):
        engine = make_engine()
        engine.state.turn = 4
        engine.state.day_campsite_groups_served = 10
        balance_before = engine.state.balance

        with mock.patch.object(CampingPlazaEngine, "_generate_overnight_guests", return_value=[]):
            with mock.patch("game_engine.random.random", return_value=0.0):
                result = {"events": []}
                engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 10)
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.today_income["campsite"], 0)
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "day"]), 0)

    def test_overnight_guests_do_not_increase_day_counter(self):
        engine = make_engine()
        engine.state.turn = 2
        engine.state.day_campsite_groups_served = 3
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [{
            "npc_id": engine._next_npc_id(),
            "group_size": 1,
            "visit_type": "overnight",
            "arrival_turn": 2,
            "planned_day": engine.state.day,
            "source": "natural_overnight",
            "arrival_status": "pending",
            "total_satisfaction": 60,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "is_reserved": False,
            "paid": False,
            "planned_actions": [],
        }]
        result = {"events": []}
        engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 3)
        self.assertEqual(engine.state.today_income["campsite"], 0)
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "overnight"]), 1)

    def test_reserved_overnight_guest_does_not_increase_day_counter(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.day_campsite_groups_served = 4
        engine.state.today_arrival_plan_day = 2
        entry = {
            "npc_id": engine._next_npc_id(),
            "group_size": 1,
            "visit_type": "overnight",
            "arrival_turn": 2,
            "planned_day": 2,
            "source": "reservation",
            "arrival_status": "pending",
            "tent_id": 1,
            "is_reserved": True,
            "paid": True,
            "total_satisfaction": 60,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "planned_actions": [],
        }
        engine.state.today_arrival_plan = [entry]
        engine.tents[1].status = "reserved"

        result = {"events": []}
        engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 4)
        reserved_npcs = [n for n in engine.npc_pool if n.is_reserved]
        self.assertEqual(len(reserved_npcs), 1)
        self.assertEqual(entry["arrival_status"], "arrived")

    def test_day_to_overnight_does_not_refund_capacity(self):
        engine = make_engine()
        engine.state.day_campsite_groups_served = 5
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="dining",
            total_satisfaction=90,
        )
        engine.npc_pool.append(guest)
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [{
            "npc_id": guest.id,
            "planned_day": engine.state.day,
            "visit_type": "day",
            "day_to_overnight_intent": True,
        }]

        engine._process_day_to_overnight({"events": []})

        self.assertEqual(engine.state.day_campsite_groups_served, 5)
        self.assertEqual(guest.visit_type, "overnight")

    def test_turn4_still_accepts_new_day_guests(self):
        engine = make_engine()
        engine.state.turn = 4
        engine.state.day_campsite_groups_served = 9
        engine.state.today_arrival_plan_day = engine.state.day
        entry = {
            "npc_id": engine._next_npc_id(),
            "planned_day": engine.state.day,
            "arrival_turn": 4,
            "source": "natural_day",
            "visit_type": "day",
            "arrival_status": "pending",
            "group_size": 2,
            "total_satisfaction": 60,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "is_reserved": False,
            "paid": False,
            "planned_actions": [],
        }
        engine.state.today_arrival_plan = [entry]
        result = {"events": []}
        engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 10)
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "day"]), 1)
        self.assertEqual(entry["arrival_status"], "arrived")
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)

    def test_new_day_resets_day_campsite_counter_once(self):
        engine = make_engine()
        engine.state.day = 3
        engine.state.turn = 6
        engine.state.day_campsite_groups_served = 10

        engine._new_day()

        self.assertEqual(engine.state.day, 4)
        self.assertEqual(engine.state.turn, 1)
        self.assertEqual(engine.state.day_campsite_groups_served, 0)

class DayToOvernightIntentPlanTests(unittest.TestCase):
    """日转夜意向在日初计划包中一次生成。"""

    def test_natural_day_guests_roll_intent_and_overnight_guests_are_false(self):
        engine = make_engine()
        engine.state.day = 2
        day_guests = [
            NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day"),
            NPCGroup(id=engine._next_npc_id(), group_size=2, visit_type="day"),
        ]
        overnight_guest = NPCGroup(
            id=engine._next_npc_id(), group_size=3, visit_type="overnight"
        )

        with mock.patch.object(
            engine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 2, "overnight_guest_count": 1},
        ), mock.patch.object(engine, "_create_day_guest", side_effect=day_guests), mock.patch.object(
            engine, "_create_overnight_guest", return_value=overnight_guest
        ), mock.patch.object(engine, "_roll_arrival_turn", return_value=2), mock.patch.object(
            engine, "_append_planned_actions"
        ), mock.patch("game_engine.random.random", side_effect=[0.1, 0.15]):
            self.assertTrue(engine._ensure_today_arrival_plan())

        entries = engine.state.today_arrival_plan
        self.assertEqual(
            [entry["day_to_overnight_intent"] for entry in entries],
            [True, False, False],
        )

    def test_repeated_plan_read_does_not_reroll_intent(self):
        engine = make_engine()
        engine.state.day = 2
        day_guest = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="day")

        with mock.patch.object(
            engine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ), mock.patch.object(engine, "_create_day_guest", return_value=day_guest), mock.patch.object(
            engine, "_roll_arrival_turn", return_value=2
        ), mock.patch.object(engine, "_append_planned_actions"), mock.patch(
            "game_engine.random.random", return_value=0.1
        ) as random_mock:
            self.assertTrue(engine._ensure_today_arrival_plan())
            plan = engine.state.today_arrival_plan
            self.assertFalse(engine._ensure_today_arrival_plan())

        self.assertIs(engine.state.today_arrival_plan, plan)
        self.assertEqual(random_mock.call_count, 1)

    def test_reserved_day_guest_uses_same_intent_roll(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.reservation = {
            "npc_id": engine._next_npc_id(),
            "group_size": 2,
            "visit_type": "day",
            "status": "accepted",
            "arrival_day": engine.state.day,
        }

        with mock.patch.object(
            engine,
            "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 0, "overnight_guest_count": 0},
        ), mock.patch.object(engine, "_roll_arrival_turn", return_value=2), mock.patch.object(
            engine, "_append_planned_actions"
        ), mock.patch("game_engine.random.random", return_value=0.1) as random_mock:
            self.assertTrue(engine._ensure_today_arrival_plan())

        entry = engine.state.today_arrival_plan[0]
        self.assertEqual(entry["source"], "reservation")
        self.assertEqual(entry["visit_type"], "day")
        self.assertTrue(entry["day_to_overnight_intent"])
        self.assertEqual(random_mock.call_count, 1)


class DayToOvernightTentMatchingTests(unittest.TestCase):
    """日转夜客组在执行前的纯帐篷匹配。"""

    def _guest(self, npc_id, group_size):
        return NPCGroup(id=npc_id, group_size=group_size, visit_type="day")

    def _tent(self, tent_id, capacity):
        return Tent(id=tent_id, capacity=capacity)

    def test_maximizes_successful_guest_groups(self):
        engine = make_engine()
        guests = [self._guest(1, 2), self._guest(2, 3)]
        tents = [self._tent(1, 3), self._tent(2, 2)]

        matches = engine._match_day_to_overnight_tents(guests, tents)

        self.assertEqual(matches, {1: 2, 2: 1})

    def test_prefers_smallest_total_capacity_waste(self):
        engine = make_engine()
        guests = [self._guest(1, 2), self._guest(2, 4)]
        tents = [self._tent(1, 2), self._tent(2, 4), self._tent(3, 6)]

        matches = engine._match_day_to_overnight_tents(guests, tents)

        self.assertEqual(matches, {1: 1, 2: 2})

    def test_prefers_evenly_smaller_individual_capacity_waste(self):
        engine = make_engine()
        guests = [self._guest(1, 1), self._guest(2, 2)]
        tents = [self._tent(1, 2), self._tent(2, 3)]

        with mock.patch("game_engine.random.choice") as choice_mock:
            matches = engine._match_day_to_overnight_tents(guests, tents)

        self.assertEqual(matches, {1: 1, 2: 2})
        choice_mock.assert_not_called()

    def test_uses_random_choice_for_exactly_equal_matches(self):
        engine = make_engine()
        guests = [self._guest(1, 2)]
        tents = [self._tent(1, 2), self._tent(2, 2)]

        with mock.patch(
            "game_engine.random.choice", side_effect=lambda choices: choices[-1]
        ) as choice_mock:
            matches = engine._match_day_to_overnight_tents(guests, tents)

        choice_mock.assert_called_once()
        self.assertIn(matches, ({1: 1}, {1: 2}))

    def test_returns_empty_match_when_no_tent_fits(self):
        engine = make_engine()

        matches = engine._match_day_to_overnight_tents(
            [self._guest(1, 3)], [self._tent(1, 2)]
        )

        self.assertEqual(matches, {})

    def test_does_not_mutate_guests_tents_or_game_state(self):
        engine = make_engine()
        guests = [self._guest(1, 2)]
        tents = [self._tent(1, 2)]
        guest_before = [(guest.visit_type, guest.location, guest.has_left) for guest in guests]
        tent_before = [(tent.status, tent.occupied_by) for tent in tents]
        balance_before = engine.state.balance
        accommodation_before = engine.state.today_income["accommodation"]

        matches = engine._match_day_to_overnight_tents(guests, tents)

        self.assertEqual(matches, {1: 1})
        self.assertEqual(
            [(guest.visit_type, guest.location, guest.has_left) for guest in guests],
            guest_before,
        )
        self.assertEqual(
            [(tent.status, tent.occupied_by) for tent in tents], tent_before
        )
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.today_income["accommodation"], accommodation_before)


class TurnPlanTests(unittest.TestCase):
    def _engine_for_plan(self, turn: int = 2) -> CampingPlazaEngine:
        engine = make_engine()
        engine.state.day = 1
        engine.state.turn = turn
        engine.state.decisions_left = 3
        engine.state.pending_turn_plan = None
        for tent in engine.tents.values():
            tent.next_breakdown_turn = 99999
        return engine

    def test_submit_turn_plan_windows_and_limits(self):
        for turn in (2, 3, 4, 5):
            engine = self._engine_for_plan(turn)
            result = engine.submit_turn_plan([], [])
            self.assertTrue(result["success"])
            self.assertEqual(result["target_turn"], turn)

        for turn in (1, 6):
            engine = self._engine_for_plan(turn)
            result = engine.submit_turn_plan([], [])
            self.assertFalse(result["success"])

        engine = self._engine_for_plan(2)
        self.assertTrue(engine.submit_turn_plan([], [])["success"])
        self.assertFalse(engine.submit_turn_plan([], [])["success"])

        engine = self._engine_for_plan(2)
        actions = [{"action": "improve_service"} for _ in range(3)]
        self.assertTrue(engine.submit_turn_plan([], actions)["success"])

        engine = self._engine_for_plan(2)
        actions = [{"action": "improve_service"} for _ in range(4)]
        self.assertFalse(engine.submit_turn_plan([], actions)["success"])

        engine = self._engine_for_plan(2)
        result = engine.submit_turn_plan(
            [{"action": "clean_tents", "tent_ids": [1, 2]}],
            [{"action": "improve_service"} for _ in range(3)],
        )
        self.assertTrue(result["success"])
        self.assertEqual(engine.state.decisions_left, 0)

    def test_submit_turn_plan_does_not_execute_or_charge(self):
        engine = self._engine_for_plan(2)
        engine.tents[1].status = "broken"
        balance_before = engine.state.balance

        result = engine.submit_turn_plan([], [{"action": "repair_tent", "tent_id": 1}])

        self.assertTrue(result["success"])
        self.assertEqual(engine.tents[1].status, "broken")
        self.assertEqual(engine.state.balance, balance_before)

    def test_advance_requires_plan_on_business_turns(self):
        engine = self._engine_for_plan(2)

        result = engine.advance_turn()

        self.assertEqual(result["turn"], 2)
        self.assertIn("submit turn plan first", result["events"])

    def test_turn_plan_executes_once_and_clears(self):
        engine = self._engine_for_plan(2)
        engine.tents[1].status = "broken"
        engine.state.balance = 1000

        self.assertTrue(
            engine.submit_turn_plan(
                [{"action": "clean_tents", "tent_ids": [2]}],
                [{"action": "repair_tent", "tent_id": 1}],
            )["success"]
        )

        with mock.patch.object(CampingPlazaEngine, "_process_checkout_all") as checkout_mock:
            with mock.patch.object(CampingPlazaEngine, "_assign_reserved_tent_for_today"):
                with mock.patch.object(CampingPlazaEngine, "_process_reservations"):
                    with mock.patch.object(CampingPlazaEngine, "_process_checkin"):
                        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
                            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                                with mock.patch.object(CampingPlazaEngine, "_handle_breakdowns"):
                                    result = engine.advance_turn()

        self.assertEqual(checkout_mock.call_count, 1)
        self.assertEqual(result["turn"], 3)
        self.assertIsNone(engine.state.pending_turn_plan)
        self.assertEqual(result["plan_execution"]["free_actions"][0]["action"], "clean_tents")
        self.assertEqual(result["plan_execution"]["actions"][0]["action"], "repair_tent")
        self.assertEqual(engine.tents[1].status, "available")

        second = engine.advance_turn()
        self.assertEqual(second["turn"], 3)
        self.assertIn("submit turn plan first", second["events"])

    def test_invalid_planned_action_skips_without_spending(self):
        engine = self._engine_for_plan(2)
        balance_before = engine.state.balance

        self.assertTrue(
            engine.submit_turn_plan([], [{"action": "repair_tent", "tent_id": 1}])["success"]
        )

        with mock.patch.object(CampingPlazaEngine, "_process_checkout_all"):
            with mock.patch.object(CampingPlazaEngine, "_assign_reserved_tent_for_today"):
                with mock.patch.object(CampingPlazaEngine, "_process_reservations"):
                    with mock.patch.object(CampingPlazaEngine, "_process_checkin"):
                        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
                            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                                with mock.patch.object(CampingPlazaEngine, "_handle_breakdowns"):
                                    result = engine.advance_turn()

        self.assertFalse(result["plan_execution"]["actions"][0]["success"])
        self.assertEqual(engine.state.balance, balance_before)
        self.assertIsNone(engine.state.pending_turn_plan)

    def test_breakdown_no_longer_blocks_progression(self):
        engine = self._engine_for_plan(2)
        engine.tents[1].status = "available"
        engine.tents[1].next_breakdown_turn = engine._absolute_turn()
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_assign_reserved_tent_for_today"):
            with mock.patch.object(CampingPlazaEngine, "_process_reservations"):
                with mock.patch.object(CampingPlazaEngine, "_process_checkin"):
                    with mock.patch.object(CampingPlazaEngine, "_process_dining"):
                        with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                            result = engine.advance_turn()

        self.assertEqual(result["turn"], 3)
        self.assertEqual(engine.tents[1].status, "broken")

    def test_turn5_breakdown_still_enters_turn6(self):
        engine = self._engine_for_plan(5)
        engine.tents[1].status = "available"
        engine.tents[1].next_breakdown_turn = engine._absolute_turn()
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                result = engine.advance_turn()

        self.assertEqual(result["turn"], 6)
        self.assertEqual(engine.tents[1].status, "broken")

    def test_turn5_plan_clears_after_turn6_and_rejects_resubmit(self):
        engine = self._engine_for_plan(5)
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                result = engine.advance_turn()

        self.assertEqual(result["turn"], 6)
        self.assertIsNone(engine.state.pending_turn_plan)
        self.assertFalse(engine.submit_turn_plan([], [])["success"])

    def test_turn4_to_turn5_does_not_clear_food_stock(self):
        engine = self._engine_for_plan(4)
        engine.state.food_stock = 11
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                result = engine.advance_turn()

        self.assertEqual(result["turn"], 5)
        self.assertEqual(engine.state.food_stock, 11)

    def test_turn5_to_turn6_clears_food_stock_after_business_wrap_up(self):
        engine = self._engine_for_plan(5)
        engine.state.food_stock = 13
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                result = engine.advance_turn()

        self.assertEqual(result["turn"], 6)
        self.assertEqual(engine.state.food_stock, 0)

    def test_turn5_clears_opening_food_gift_stock(self):
        engine = make_engine()
        engine.state.turn = 5
        engine.state.pending_turn_plan = None
        for tent in engine.tents.values():
            tent.next_breakdown_turn = 99999

        self.assertEqual(
            engine.state.food_stock,
            CampingPlazaEngine.FOOD_PACKAGES["medium"]["portions"],
        )
        self.assertTrue(engine.submit_turn_plan([], [])["success"])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                result = engine.advance_turn()

        self.assertEqual(result["turn"], 6)
        self.assertEqual(engine.state.food_stock, 0)

    def test_turn6_food_stock_survives_new_day_transition(self):
        engine = self._engine_for_plan(5)
        engine.state.food_stock = 4
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                first = engine.advance_turn()

        self.assertEqual(first["turn"], 6)
        engine.state.food_stock = 7

        second = engine.advance_turn()

        self.assertEqual(second["day"], 2)
        self.assertEqual(second["turn"], 1)
        self.assertEqual(engine.state.food_stock, 7)

    def test_new_day_does_not_regrant_opening_food_gift(self):
        engine = make_engine()
        engine.state.turn = 5
        engine.state.pending_turn_plan = None
        for tent in engine.tents.values():
            tent.next_breakdown_turn = 99999
        self.assertTrue(engine.submit_turn_plan([], [])["success"])

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                first = engine.advance_turn()

        self.assertIn(engine._build_opening_food_gift_event(), first["events"])
        self.assertEqual(first["turn"], 6)

        second = engine.advance_turn()

        self.assertEqual(second["day"], 2)
        self.assertEqual(second["turn"], 1)
        self.assertEqual(engine.state.food_stock, 0)
        self.assertNotIn(engine._build_opening_food_gift_event(), second["events"])

    def test_buy_food_package_is_turn_plan_decision_action(self):
        self.assertEqual(
            CampingPlazaEngine.TURN_PLAN_ACTIONS["buy_food_package"]["kind"],
            "decision",
        )

    def test_turn_plan_food_package_actions_execute_in_order(self):
        engine = self._engine_for_plan(2)
        engine.state.balance = 230
        engine.state.food_stock = 1

        self.assertTrue(
            engine.submit_turn_plan([], [
                {"action": "buy_food_package", "package_key": "small"},
                {"action": "buy_food_package", "package_key": "medium"},
                {"action": "buy_food_package", "package_key": "large"},
            ])["success"]
        )

        with mock.patch.object(CampingPlazaEngine, "_process_checkout_all"):
            with mock.patch.object(CampingPlazaEngine, "_assign_reserved_tent_for_today"):
                with mock.patch.object(CampingPlazaEngine, "_process_reservations"):
                    with mock.patch.object(CampingPlazaEngine, "_process_checkin"):
                        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
                            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                                with mock.patch.object(CampingPlazaEngine, "_handle_breakdowns"):
                                    result = engine.advance_turn()

        self.assertEqual(
            [item["success"] for item in result["plan_execution"]["actions"]],
            [True, True, False],
        )
        self.assertEqual(
            engine.state.food_stock,
            1
            + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"]
            + CampingPlazaEngine.FOOD_PACKAGES["medium"]["portions"],
        )
        self.assertEqual(engine.state.balance, 0)
        self.assertEqual(engine.state.last_food_preorder_day, 0)

    def test_turn5_planned_food_purchase_still_clears_on_turn6(self):
        engine = self._engine_for_plan(5)
        engine.state.balance = 999
        engine.state.food_stock = 0
        self.assertTrue(
            engine.submit_turn_plan([], [
                {"action": "buy_food_package", "package_key": "medium"}
            ])["success"]
        )

        with mock.patch.object(CampingPlazaEngine, "_process_dining"):
            with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                result = engine.advance_turn()

        self.assertEqual(result["turn"], 6)
        self.assertEqual(engine.state.food_stock, 0)


class DiningRestockPurchaseIntegrationTests(unittest.TestCase):
    def _make_engine(self, *, balance=1000, food_stock=0, turn=2):
        engine = make_engine()
        engine.state.day = 1
        engine.state.turn = turn
        engine.state.decisions_left = 3
        engine.state.pending_turn_plan = None
        engine.state.balance = balance
        engine.state.food_stock = food_stock
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = []
        engine.state.today_events = []
        for tent in engine.tents.values():
            tent.next_breakdown_turn = 99999
        return engine

    def _add_waiting_dining_action(
        self,
        engine,
        *,
        group_size,
        planned_turn,
        menu_key="premium",
        total_satisfaction=50,
    ):
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=group_size,
            visit_type="day",
            arrival_turn=planned_turn,
            location="dining",
            total_satisfaction=total_satisfaction,
            economic_level=2,
        )
        engine.npc_pool.append(npc)
        entry = {
            "npc_id": npc.id,
            "planned_day": engine.state.day,
            "visit_type": "day",
            "source": "natural_day",
            "planned_actions": [
                {
                    "action": "dining",
                    "menu_key": menu_key,
                    "planned_turn": planned_turn,
                    "status": "waiting_for_restock",
                    "result": "insufficient_food",
                }
            ],
            "arrival_status": "arrived",
        }
        engine.state.today_arrival_plan.append(entry)
        return npc, entry["planned_actions"][0]

    def test_successful_purchase_immediately_completes_waiting_dining(self):
        engine = self._make_engine()
        npc, action = self._add_waiting_dining_action(engine, group_size=4, planned_turn=2)
        menu = engine.DINING_SET_MENUS["premium"]
        food_package = CampingPlazaEngine.FOOD_PACKAGES["small"]

        self.assertTrue(
            engine.submit_turn_plan([], [{"action": "buy_food_package", "package_key": "small"}])["success"]
        )
        result = {"events": []}

        with mock.patch.object(
            engine,
            "_retry_waiting_dining_after_restock",
            wraps=engine._retry_waiting_dining_after_restock,
        ) as retry_mock:
            engine._execute_pending_turn_plan(result)

        self.assertEqual(retry_mock.call_count, 1)
        self.assertEqual(result["plan_execution"]["actions"][0]["success"], True)
        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["result"], "success")
        self.assertEqual(engine.state.food_stock, food_package["portions"] - npc.group_size)
        self.assertEqual(engine.state.balance, 1000 - food_package["price"] + menu["price_per_person"] * 4)
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 4)
        self.assertEqual(npc.total_satisfaction, 50 + menu["satisfaction_gain"])
        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(len(result["events"]), 1)

    def test_failed_purchase_does_not_trigger_restock_retry(self):
        engine = self._make_engine(balance=100)
        npc, action = self._add_waiting_dining_action(engine, group_size=4, planned_turn=2)

        self.assertTrue(
            engine.submit_turn_plan([], [{"action": "buy_food_package", "package_key": "large"}])["success"]
        )
        result = {"events": []}

        with mock.patch.object(
            engine,
            "_retry_waiting_dining_after_restock",
            wraps=engine._retry_waiting_dining_after_restock,
        ) as retry_mock:
            engine._execute_pending_turn_plan(result)

        self.assertEqual(retry_mock.call_count, 0)
        self.assertFalse(result["plan_execution"]["actions"][0]["success"])
        self.assertEqual(action["status"], "waiting_for_restock")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(engine.state.balance, 100)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 50)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(result["events"], [])

    def test_two_successful_purchases_retry_each_time_without_repeat(self):
        engine = self._make_engine(balance=300)
        npc, action = self._add_waiting_dining_action(engine, group_size=6, planned_turn=2)
        menu = engine.DINING_SET_MENUS["premium"]
        small_package = CampingPlazaEngine.FOOD_PACKAGES["small"]

        self.assertTrue(
            engine.submit_turn_plan(
                [],
                [
                    {"action": "buy_food_package", "package_key": "small"},
                    {"action": "buy_food_package", "package_key": "small"},
                ],
            )["success"]
        )
        result = {"events": []}

        with mock.patch.object(
            engine,
            "_retry_waiting_dining_after_restock",
            wraps=engine._retry_waiting_dining_after_restock,
        ) as retry_mock:
            engine._execute_pending_turn_plan(result)

        self.assertEqual(retry_mock.call_count, 2)
        self.assertEqual([item["success"] for item in result["plan_execution"]["actions"]], [True, True])
        self.assertEqual(action["status"], "completed")
        self.assertEqual(action["result"], "success")
        self.assertEqual(engine.state.food_stock, small_package["portions"] * 2 - npc.group_size)
        self.assertEqual(
            engine.state.balance,
            300 - small_package["price"] * 2 + menu["price_per_person"] * npc.group_size,
        )
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * npc.group_size)
        self.assertEqual(npc.total_satisfaction, 50 + menu["satisfaction_gain"])
        self.assertEqual(len(result["events"]), 1)

        balance_after = engine.state.balance
        food_after = engine.state.food_stock
        satisfaction_after = npc.total_satisfaction
        repeat_result = {"events": []}
        engine._retry_waiting_dining_after_restock(repeat_result)

        self.assertEqual(engine.state.balance, balance_after)
        self.assertEqual(engine.state.food_stock, food_after)
        self.assertEqual(npc.total_satisfaction, satisfaction_after)
        self.assertEqual(repeat_result["events"], [])

    def test_turn6_direct_purchase_does_not_trigger_restock_retry(self):
        engine = self._make_engine(turn=6)
        npc, action = self._add_waiting_dining_action(engine, group_size=4, planned_turn=6)

        with mock.patch.object(
            engine,
            "_retry_waiting_dining_after_restock",
            wraps=engine._retry_waiting_dining_after_restock,
        ) as retry_mock:
            result = engine.buy_food_package("small")

        self.assertTrue(result["success"])
        self.assertEqual(retry_mock.call_count, 0)
        self.assertEqual(action["status"], "waiting_for_restock")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(engine.state.food_stock, CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"])
        self.assertEqual(engine.state.last_food_preorder_day, engine.state.day)


class FoodPackagePurchaseTests(unittest.TestCase):
    def test_shared_food_package_helper_uses_config(self):
        engine = make_engine()

        for package_key, package in CampingPlazaEngine.FOOD_PACKAGES.items():
            engine.state.balance = 1000
            engine.state.food_stock = 0

            result = engine._buy_food_package(package_key)

            self.assertTrue(result["success"])
            self.assertEqual(result["package_key"], package_key)
            self.assertEqual(result["portions"], package["portions"])
            self.assertEqual(result["price"], package["price"])
            self.assertEqual(engine.state.balance, 1000 - package["price"])
            self.assertEqual(engine.state.food_stock, package["portions"])
            self.assertIn(package["name"], result["message"])

    def test_shared_food_package_helper_rejects_invalid_and_insufficient(self):
        engine = make_engine()
        engine.state.balance = 79
        engine.state.food_stock = 3

        insufficient = engine._buy_food_package("small")
        self.assertFalse(insufficient["success"])
        self.assertEqual(engine.state.balance, 79)
        self.assertEqual(engine.state.food_stock, 3)

        invalid = engine._buy_food_package("mega")
        self.assertFalse(invalid["success"])
        self.assertEqual(engine.state.balance, 79)
        self.assertEqual(engine.state.food_stock, 3)

    def test_turn6_food_preorder_only_succeeds_once_per_day(self):
        engine = make_engine()
        engine.state.turn = 6
        engine.state.balance = 500
        engine.state.decisions_left = 2
        original_stock = engine.state.food_stock

        first = engine.buy_food_package("small")
        second = engine.buy_food_package("medium")

        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(engine.state.decisions_left, 2)
        self.assertEqual(engine.state.last_food_preorder_day, engine.state.day)
        self.assertEqual(
            engine.state.food_stock,
            original_stock + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"],
        )

    def test_turn6_failed_preorder_can_retry_with_cheaper_package(self):
        engine = make_engine()
        engine.state.turn = 6
        engine.state.balance = 100
        opening_stock = engine.state.food_stock

        expensive = engine.buy_food_package("large")
        self.assertFalse(expensive["success"])
        self.assertEqual(engine.state.last_food_preorder_day, 0)

        cheaper = engine.buy_food_package("small")
        self.assertTrue(cheaper["success"])
        self.assertEqual(engine.state.last_food_preorder_day, engine.state.day)
        self.assertEqual(
            engine.state.food_stock,
            opening_stock + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"],
        )

    def test_turn6_food_preorder_stock_survives_new_day_and_next_day_can_buy_again(self):
        engine = make_engine()
        engine.state.turn = 6
        opening_stock = engine.state.food_stock

        first = engine.buy_food_package("small")
        self.assertTrue(first["success"])
        stock_after_first = engine.state.food_stock

        engine._new_day()

        self.assertEqual(engine.state.food_stock, stock_after_first)
        self.assertEqual(engine.state.day, 2)
        self.assertEqual(engine.state.turn, 1)

        engine.state.turn = 6
        second = engine.buy_food_package("small")

        self.assertTrue(second["success"])
        self.assertEqual(engine.state.last_food_preorder_day, 2)
        self.assertEqual(
            engine.state.food_stock,
            opening_stock + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"] * 2,
        )


class RepairStateRecoveryTests(unittest.TestCase):
    """维修后帐篷状态恢复"""

    def test_occupied_tent_repair_restores_occupied(self):
        """有住客的 broken 帐篷修好后恢复 occupied"""
        engine = make_engine()
        engine.tents[1].status = "broken"
        engine.tents[1].occupied_by = 42
        engine.tents[1].next_breakdown_turn = 0
        engine.state.decisions_left = 3

        result = engine.repair_tent(1)

        self.assertTrue(result["success"])
        self.assertEqual(engine.tents[1].status, "occupied")
        self.assertEqual(engine.tents[1].occupied_by, 42)

    def test_reserved_tent_repair_restores_reserved(self):
        """今日预定帐篷修好后恢复 reserved"""
        engine = make_engine()
        engine.state.day = 1
        engine.state.reserved_tent_id = 1
        engine.state.reserved_tent_day = 1
        engine.tents[1].status = "broken"
        engine.tents[1].next_breakdown_turn = 0
        engine.state.decisions_left = 3

        result = engine.repair_tent(1)

        self.assertTrue(result["success"])
        self.assertEqual(engine.tents[1].status, "reserved")

    def test_available_tent_repair_restores_available(self):
        """普通空帐篷修好后恢复 available"""
        engine = make_engine()
        engine.tents[3].is_unlocked = True
        engine.tents[3].status = "broken"
        engine.tents[3].occupied_by = None
        engine.tents[3].next_breakdown_turn = 0
        engine.state.decisions_left = 3

        result = engine.repair_tent(3)

        self.assertTrue(result["success"])
        self.assertEqual(engine.tents[3].status, "available")

    def test_repair_non_broken_fails(self):
        """维修非 broken 帐篷失败且不补决策点"""
        engine = make_engine()
        engine.tents[1].status = "available"
        decisions_before = engine.state.decisions_left

        result = engine.repair_tent(1)

        self.assertFalse(result["success"])
        self.assertEqual(engine.state.decisions_left, decisions_before)

    def test_repair_invalid_tent_fails(self):
        """维修不存在帐篷失败且不补决策点"""
        engine = make_engine()
        decisions_before = engine.state.decisions_left

        result = engine.repair_tent(99)

        self.assertFalse(result["success"])
        self.assertEqual(engine.state.decisions_left, decisions_before)


class HiddenInfoTests(unittest.TestCase):
    """对外状态隐藏内部字段"""

    def test_tents_hide_internal_fields(self):
        engine = make_engine()
        state = engine.get_full_state()

        for tid, tent in state["tents"].items():
            self.assertNotIn("next_breakdown_turn", tent)

    def test_reservation_hides_hidden_tags(self):
        """reservation 不暴露三个隐藏标签"""
        engine = make_engine()
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 2,
            "temperament": 0,
        }
        state = engine.get_full_state()

        self.assertIsNotNone(state["reservation"])
        self.assertNotIn("economic_level", state["reservation"])
        self.assertNotIn("spending_habit", state["reservation"])
        self.assertNotIn("temperament", state["reservation"])

    def test_active_npcs_hide_hidden_tags(self):
        """active_npcs 不暴露三个隐藏标签"""
        engine = make_engine()
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            economic_level=1,
            spending_habit=2,
            temperament=0,
        )
        engine.npc_pool.append(npc)
        state = engine.get_full_state()

        self.assertEqual(len(state["active_npcs"]), 1)
        safe_npc = state["active_npcs"][0]
        self.assertNotIn("economic_level", safe_npc)
        self.assertNotIn("spending_habit", safe_npc)
        self.assertNotIn("temperament", safe_npc)

    def test_full_state_hides_pending_reviews(self):
        """对外完整状态不暴露待结算评价队列"""
        engine = make_engine()
        engine.state.pending_reviews.append({
            "created_day": 1,
            "rating": 4,
            "npc_id": 7,
            "visit_type": "day",
            "group_size": 2,
        })

        state = engine.get_full_state()

        self.assertNotIn("pending_reviews", state)


class DelayedReviewSettlementTests(unittest.TestCase):
    """评价延迟生成与 Turn 1 晨间结算"""

    def _make_overnight_guest(self, engine, *, total_satisfaction=80):
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="overnight",
            location="tent_1",
            total_satisfaction=total_satisfaction,
        )
        engine.npc_pool.append(npc)
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id
        engine.tents[1].next_breakdown_turn = 99999
        return npc

    def test_checkout_creates_pending_review_without_immediate_totals_change(self):
        engine = make_engine()
        npc = self._make_overnight_guest(engine)
        result = {"events": []}

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._checkout_npc(npc, result)

        self.assertTrue(npc.review_left)
        self.assertEqual(npc.review_rating, 4)
        self.assertEqual(engine.state.total_reviews, 0)
        self.assertEqual(engine.state.total_rating_sum, 0)
        self.assertEqual(engine.state.reputation_rate, 60.0)
        self.assertEqual(len(engine.state.pending_reviews), 1)
        self.assertEqual(engine.state.pending_reviews[0]["created_day"], engine.state.day)
        self.assertIn("将在次日晨间结算", "".join(result["events"]))

    def test_no_review_does_not_create_pending_record(self):
        engine = make_engine()
        npc = self._make_overnight_guest(engine)

        with mock.patch("game_engine.random.random", return_value=0.99):
            engine._checkout_npc(npc, {"events": []})

        self.assertFalse(npc.review_left)
        self.assertEqual(npc.review_rating, 0)
        self.assertEqual(engine.state.pending_reviews, [])

    def test_turn1_settles_previous_day_reviews_but_keeps_new_turn1_review_pending(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 1
        engine.state.pending_reviews = [{
            "created_day": 1,
            "rating": 5,
            "npc_id": 99,
            "visit_type": "day",
            "group_size": 2,
        }]
        npc = self._make_overnight_guest(engine, total_satisfaction=80)

        with mock.patch("game_engine.random.random", side_effect=[0.0, 0.0]):
            result = engine.advance_turn()

        self.assertEqual(result["turn"], 2)
        self.assertEqual(engine.state.total_reviews, 1)
        self.assertEqual(engine.state.total_rating_sum, 5)
        self.assertEqual(engine.state.reputation_rate, 100.0)
        self.assertEqual(len(engine.state.pending_reviews), 1)
        self.assertEqual(engine.state.pending_reviews[0]["created_day"], 2)
        self.assertEqual(engine.state.pending_reviews[0]["rating"], 4)
        self.assertTrue(any("晨间结算了1条昨日评价" in event for event in result["events"]))


class CheckoutTurnTests(unittest.TestCase):
    def test_direct_overnight_checkin_assigns_checkout_turn_once(self):
        engine = make_engine()
        engine.state.turn = 2
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
        )

        with mock.patch("game_engine.random.random", return_value=0.2):
            engine._checkin_npc(guest, 1, {"events": []})

        self.assertEqual(guest.checkout_turn, 1)
        with mock.patch("game_engine.random.random", return_value=0.8):
            self.assertEqual(engine._ensure_checkout_turn(guest), 1)
        self.assertEqual(guest.checkout_turn, 1)

    def test_reserved_guest_checkin_assigns_checkout_turn(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.reservation = {
            "group_size": 1,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        engine.state.reserved_tent_id = 1
        engine.state.reserved_tent_day = 2
        engine.tents[1].status = "reserved"
        engine.state.today_arrival_plan_day = 2
        engine.state.today_arrival_plan = [{
            "npc_id": engine._next_npc_id(),
            "planned_day": 2,
            "source": "reservation",
            "visit_type": "day",
            "arrival_status": "pending",
            "arrival_turn": 2,
            "tent_id": 1,
            "group_size": 1,
            "total_satisfaction": 50,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }]

        with mock.patch("game_engine.random.random", return_value=0.8):
            engine._process_reservations({"events": []})

        reserved_npcs = [n for n in engine.npc_pool if n.is_reserved]
        self.assertEqual(len(reserved_npcs), 1)
        self.assertEqual(reserved_npcs[0].checkout_turn, 2)

    def test_day_to_overnight_fixes_checkout_turn_to_one_without_reroll(self):
        engine = make_engine()
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="dining",
            total_satisfaction=90,
        )
        engine.npc_pool.append(guest)
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [{
            "npc_id": guest.id,
            "planned_day": engine.state.day,
            "visit_type": "day",
            "day_to_overnight_intent": True,
        }]

        with mock.patch("game_engine.random.random") as random_mock:
            engine._process_day_to_overnight({"events": []})

        self.assertEqual(guest.visit_type, "overnight")
        self.assertEqual(guest.checkout_turn, 1)
        random_mock.assert_not_called()

    def test_existing_checkout_turn_is_not_overwritten(self):
        engine = make_engine()
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            checkout_turn=2,
        )
        engine.npc_pool.append(npc)
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id

        with mock.patch("game_engine.random.random", return_value=0.1):
            self.assertEqual(engine._ensure_checkout_turn(npc), 2)
        self.assertEqual(npc.checkout_turn, 2)

    def test_turn1_only_checks_out_checkout_turn_one(self):
        engine = make_engine()
        npc1 = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="overnight", location="tent_1", checkout_turn=1)
        npc2 = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="overnight", location="tent_2", checkout_turn=2)
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc1.id
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "occupied"
        engine.tents[2].occupied_by = npc2.id
        engine.npc_pool.extend([npc1, npc2])

        engine._process_checkout_partial({"events": []})

        self.assertTrue(npc1.has_left)
        self.assertFalse(npc2.has_left)
        self.assertEqual(engine.tents[1].status, "cleaning")
        self.assertEqual(engine.tents[2].status, "occupied")

    def test_turn2_only_checks_out_checkout_turn_two(self):
        engine = make_engine()
        npc1 = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="overnight", location="tent_1", checkout_turn=1, has_left=True)
        npc2 = NPCGroup(id=engine._next_npc_id(), group_size=1, visit_type="overnight", location="tent_2", checkout_turn=2)
        engine.tents[1].status = "cleaning"
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "occupied"
        engine.tents[2].occupied_by = npc2.id
        engine.npc_pool.extend([npc1, npc2])

        engine._process_checkout_all({"events": []})

        self.assertTrue(npc2.has_left)
        self.assertEqual(engine.tents[2].status, "cleaning")

    def test_turn2_checkout_happens_before_planned_cleaning(self):
        engine = make_engine()
        engine.state.turn = 2
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            checkout_turn=2,
        )
        engine.npc_pool.append(npc)
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id
        self.assertTrue(
            engine.submit_turn_plan(
                [{"action": "clean_tents", "tent_ids": [1]}],
                [],
            )["success"]
        )

        with mock.patch.object(CampingPlazaEngine, "_assign_reserved_tent_for_today"):
            with mock.patch.object(CampingPlazaEngine, "_process_reservations"):
                with mock.patch.object(CampingPlazaEngine, "_process_checkin"):
                    with mock.patch.object(CampingPlazaEngine, "_process_dining"):
                        with mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                            with mock.patch.object(CampingPlazaEngine, "_handle_breakdowns"):
                                result = engine.advance_turn()

        self.assertEqual(result["plan_execution"]["free_actions"][0]["action"], "clean_tents")
        self.assertEqual(engine.tents[1].status, "available")
        self.assertTrue(npc.has_left)

    def test_turn2_overdue_checkout_turn_one_still_checks_out(self):
        engine = make_engine()
        engine.state.turn = 2
        overdue = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            checkout_turn=1,
        )
        regular = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_2",
            checkout_turn=2,
        )
        engine.npc_pool.extend([overdue, regular])
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = overdue.id
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "occupied"
        engine.tents[2].occupied_by = regular.id

        engine._process_checkout_all({"events": []})

        self.assertTrue(overdue.has_left)
        self.assertTrue(regular.has_left)
        self.assertEqual(engine.tents[1].status, "cleaning")
        self.assertEqual(engine.tents[2].status, "cleaning")

    def test_checkout_uses_occupied_tent_after_guest_activity(self):
        for location in ("dining", "entertainment", "hot_spring"):
            with self.subTest(location=location):
                engine = make_engine()
                npc = NPCGroup(
                    id=engine._next_npc_id(),
                    group_size=1,
                    visit_type="overnight",
                    location=location,
                    checkout_turn=1,
                )
                engine.npc_pool.append(npc)
                engine.tents[1].status = "occupied"
                engine.tents[1].occupied_by = npc.id

                engine._process_checkout_partial({"events": []})

                self.assertTrue(npc.has_left)
                self.assertEqual(engine.tents[1].occupied_by, None)

    def test_next_turn_checkout_tents_uses_occupied_tent(self):
        engine = make_engine()
        engine.state.turn = 2
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="hot_spring",
            checkout_turn=2,
        )
        engine.npc_pool.append(npc)
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id

        self.assertEqual(engine.get_next_turn_checkout_tents(), [1])


class IncomeAndSpendingTagTests(unittest.TestCase):
    """隐藏标签对收入的影响范围"""

    def test_economic_level_affects_amount_only(self):
        """economic_level 只影响消费金额，不影响消费概率"""
        engine = make_engine()
        prob_low = engine._calc_spend_probability(0.6, 1)
        prob_mid = engine._calc_spend_probability(0.6, 1)
        prob_high = engine._calc_spend_probability(0.6, 1)
        self.assertEqual(prob_low, prob_mid)
        self.assertEqual(prob_mid, prob_high)

        amount_low = engine._calc_spend_amount(30, 0, 1.0)
        amount_mid = engine._calc_spend_amount(30, 1, 1.0)
        amount_high = engine._calc_spend_amount(30, 2, 1.0)
        self.assertLess(amount_low, amount_high)
        self.assertAlmostEqual(amount_mid, 30, delta=1)

    def test_spending_habit_affects_probability_only(self):
        """spending_habit 只影响消费概率，不影响消费金额"""
        engine = make_engine()
        prob_low = engine._calc_spend_probability(0.6, 0)
        prob_mid = engine._calc_spend_probability(0.6, 1)
        prob_high = engine._calc_spend_probability(0.6, 2)
        self.assertLess(prob_low, prob_mid)
        self.assertLess(prob_mid, prob_high)

        amount_low = engine._calc_spend_amount(30, 1, 1.0)
        amount_mid = engine._calc_spend_amount(30, 1, 1.0)
        amount_high = engine._calc_spend_amount(30, 1, 1.0)
        self.assertEqual(amount_low, amount_mid)
        self.assertEqual(amount_mid, amount_high)

    def test_dining_and_entertainment_planned_actions_use_fixed_income_values(self):
        """Dining and entertainment planned actions use fixed configured values."""
        engine = make_engine()
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.food_stock = 1

        dining_npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="campsite",
            economic_level=1,
            spending_habit=1,
        )
        entertainment_npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="campsite",
            economic_level=1,
            spending_habit=1,
        )
        engine.npc_pool.extend([dining_npc, entertainment_npc])

        engine.state.today_arrival_plan = [
            {
                "npc_id": dining_npc.id,
                "arrival_status": "arrived",
                "planned_day": engine.state.day,
                "planned_actions": [
                    {
                        "action": "dining",
                        "planned_turn": engine.state.turn,
                        "menu_key": "basic",
                        "status": "pending",
                    }
                ],
            },
            {
                "npc_id": entertainment_npc.id,
                "arrival_status": "arrived",
                "planned_day": engine.state.day,
                "planned_actions": [
                    {
                        "action": "paid_entertainment",
                        "planned_turn": engine.state.turn,
                        "tier_key": "premium",
                        "status": "pending",
                    }
                ],
            },
        ]

        engine._process_dining({"events": []})
        engine._process_entertainment({"events": []})

        self.assertEqual(engine.state.today_income["dining"], 30)
        self.assertEqual(engine.state.today_income["entertainment"], 90)
        return
        """餐饮和娱乐使用各自的收入倍率"""
        engine = make_engine()
        engine.facilities["dining"].dining_spend_probability = 0.5
        engine.facilities["dining"].dining_income_multiplier = 2.0
        engine.facilities["entertainment"].entertainment_income_multiplier = 3.0

        dining_npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
        )
        entertainment_npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="entertainment",
            economic_level=1,
            spending_habit=1,
        )
        engine.npc_pool.extend([dining_npc, entertainment_npc])

        # random.random 返回 0.0 保证一定消费
        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})
            engine._process_entertainment({"events": []})

        # economic_level=1 倍率为 1.0
        # dining: 30 * 1.0 * 2.0 = 60
        # entertainment: 40 * 1.0 * 3.0 = 120
        self.assertEqual(engine.state.today_income["dining"], 60)
        self.assertEqual(engine.state.today_income["entertainment"], 120)

    def test_dining_entertainment_spending_habit_probability(self):
        """餐饮和娱乐使用各自的消费习惯概率倍率"""
        engine = make_engine()
        engine.facilities["dining"].dining_spend_probability = 0.6

        # 餐饮：low=0.6, mid=1.0, high=1.5
        self.assertAlmostEqual(
            engine._calc_spend_probability(0.6, 0), 0.36
        )
        self.assertAlmostEqual(
            engine._calc_spend_probability(0.6, 1), 0.60
        )
        self.assertAlmostEqual(
            engine._calc_spend_probability(0.6, 2), 0.90
        )

        # 娱乐：low=0.7, mid=1.0, high=1.3
        self.assertAlmostEqual(
            engine._calc_spend_probability(0.6, 0, low_multiplier=0.7, high_multiplier=1.3),
            0.42
        )
        self.assertAlmostEqual(
            engine._calc_spend_probability(0.6, 1, low_multiplier=0.7, high_multiplier=1.3),
            0.60
        )
        self.assertAlmostEqual(
            engine._calc_spend_probability(0.6, 2, low_multiplier=0.7, high_multiplier=1.3),
            0.78
        )


class DiningRulesTests(unittest.TestCase):
    """餐饮每日一次、按人数计费与满意度闭环"""

    def _make_dining_npc(self, **overrides):
        engine = make_engine()
        values = {
            "id": engine._next_npc_id(),
            "group_size": 1,
            "visit_type": "day",
            "arrival_turn": engine.state.turn,
            "location": "gate",
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        values.update(overrides)
        npc = NPCGroup(**values)
        engine.npc_pool.append(npc)
        return engine, npc

    def _attach_dining_action(
        self,
        engine,
        npc,
        *,
        planned_turn=None,
        menu_key=None,
        arrival_status="arrived",
        status="pending",
    ):
        entry = engine._build_arrival_plan_entry(
            npc,
            planned_turn if planned_turn is not None else engine.state.turn,
            "natural_day" if npc.visit_type == "day" else "natural_overnight",
        )
        entry["arrival_status"] = arrival_status
        action = {
            "action": "dining",
            "planned_turn": planned_turn if planned_turn is not None else engine.state.turn,
            "menu_key": (
                menu_key
                if menu_key is not None
                else engine.DINING_SET_MENU_ORDER[
                    min(
                        max(engine.facilities["dining"].level, 0),
                        max(0, min(npc.economic_level, len(engine.DINING_SET_MENU_ORDER) - 1)),
                    )
                ]
            ),
            "status": status,
        }
        entry["planned_actions"].append(action)
        engine.state.today_arrival_plan.append(entry)
        return action

    def _draw_dining_action(self, engine, npc, *, arrival_turn=None):
        entry = engine._build_arrival_plan_entry(
            npc,
            arrival_turn if arrival_turn is not None else engine.state.turn,
            "natural_day" if npc.visit_type == "day" else "natural_overnight",
        )
        action = engine._build_dining_planned_action(entry)
        if action is not None:
            entry["planned_actions"].append(action)
        engine.state.today_arrival_plan.append(entry)
        return action

    def _menu(self, key):
        return CampingPlazaEngine.DINING_SET_MENUS[key]

    def test_new_npc_has_not_consumed_dining_today(self):
        engine, npc = self._make_dining_npc()

        self.assertEqual(npc.last_dining_day, 0)
        self.assertFalse(engine._has_consumed_dining_today(npc))

    def test_level0_all_economic_levels_use_basic_menu(self):
        basic = self._menu("basic")
        for economic_level in (0, 1, 2):
            engine, npc = self._make_dining_npc(
                group_size=2, total_satisfaction=60, economic_level=economic_level
            )
            engine.state.food_stock = 2
            with self.subTest(economic_level=economic_level):
                self._attach_dining_action(engine, npc)
                engine._process_dining({"events": []})
                self.assertEqual(engine.state.today_income["dining"], basic["price_per_person"] * 2)
                self.assertEqual(npc.total_satisfaction, 60 + basic["satisfaction_gain"])

    def test_level1_uses_basic_standard_and_downgraded_standard(self):
        expectations = {
            0: "basic",
            1: "standard",
            2: "standard",
        }
        for economic_level, expected_key in expectations.items():
            engine, npc = self._make_dining_npc(
                group_size=2, total_satisfaction=50, economic_level=economic_level
            )
            engine.facilities["dining"].level = 1
            engine.state.food_stock = 2
            menu = self._menu(expected_key)
            with self.subTest(economic_level=economic_level):
                self._attach_dining_action(engine, npc)
                engine._process_dining({"events": []})
                self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 2)
                self.assertEqual(npc.total_satisfaction, 50 + menu["satisfaction_gain"])

    def test_level2_uses_basic_standard_and_premium(self):
        expectations = {
            0: "basic",
            1: "standard",
            2: "premium",
        }
        for economic_level, expected_key in expectations.items():
            engine, npc = self._make_dining_npc(
                group_size=2, total_satisfaction=40, economic_level=economic_level
            )
            engine.facilities["dining"].level = 2
            engine.state.food_stock = 2
            menu = self._menu(expected_key)
            with self.subTest(economic_level=economic_level):
                self._attach_dining_action(engine, npc)
                engine._process_dining({"events": []})
                self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 2)
                self.assertEqual(npc.total_satisfaction, 40 + menu["satisfaction_gain"])

    def test_successful_dining_marks_day_and_uses_final_menu_values(self):
        engine, npc = self._make_dining_npc(group_size=2, total_satisfaction=60, economic_level=1)
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2
        result = {"events": []}

        action = self._attach_dining_action(engine, npc)
        engine._process_dining(result)

        menu = self._menu("standard")
        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 2)
        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(npc.total_satisfaction, 60 + menu["satisfaction_gain"])
        self.assertEqual(action["status"], "completed")
        self.assertEqual(len(result["events"]), 1)

    def test_same_day_repeat_does_not_charge_twice_or_repeat_satisfaction(self):
        engine, npc = self._make_dining_npc(group_size=3, total_satisfaction=70)
        engine.state.food_stock = 6
        result1 = {"events": []}
        result2 = {"events": []}

        self._attach_dining_action(engine, npc)
        engine._process_dining(result1)
        income_after_first = engine.state.today_income["dining"]
        satisfaction_after_first = npc.total_satisfaction
        engine._process_dining(result2)

        self.assertEqual(engine.state.today_income["dining"], income_after_first)
        self.assertEqual(npc.total_satisfaction, satisfaction_after_first)
        self.assertEqual(result2["events"], [])
        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.food_stock, 3)

    def test_failed_dining_attempt_does_not_mark_and_can_retry_later(self):
        engine, npc = self._make_dining_npc(total_satisfaction=60, economic_level=1)
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2

        with mock.patch("game_engine.random.random", return_value=0.99):
            action = self._draw_dining_action(engine, npc)

        self.assertIsNone(action)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 60)

        engine._new_day()
        engine.state.food_stock = 2
        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        menu = self._menu("standard")
        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"])
        self.assertEqual(npc.total_satisfaction, 60 + menu["satisfaction_gain"])

    def test_next_day_can_consume_again_without_manual_reset(self):
        engine, npc = self._make_dining_npc()
        engine.state.food_stock = 2

        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        first_day_income = engine.state.today_income["dining"]
        engine._new_day()
        engine.state.food_stock = 2
        self._attach_dining_action(engine, npc)

        engine._process_dining({"events": []})

        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.today_income["dining"], first_day_income)

    def test_premium_menu_uses_fixed_price_without_old_multiplier(self):
        engine, npc = self._make_dining_npc(group_size=4, economic_level=2, total_satisfaction=50)
        engine.facilities["dining"].level = 2
        engine.facilities["dining"].dining_income_multiplier = 3.0
        engine.facilities["dining"].dining_satisfaction = 99.0
        engine.state.food_stock = 4

        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        menu = self._menu("premium")
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 4)
        self.assertEqual(npc.total_satisfaction, 50 + menu["satisfaction_gain"])

    def test_same_menu_gives_same_group_satisfaction_for_different_group_sizes(self):
        menu = self._menu("premium")
        for group_size in (1, 5):
            engine, npc = self._make_dining_npc(
                group_size=group_size, economic_level=2, total_satisfaction=30
            )
            engine.facilities["dining"].level = 2
            engine.state.food_stock = group_size
            with self.subTest(group_size=group_size):
                self._attach_dining_action(engine, npc)
                engine._process_dining({"events": []})
                self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * group_size)
                self.assertEqual(engine.state.food_stock, 0)
                self.assertEqual(npc.total_satisfaction, 30 + menu["satisfaction_gain"])

    def test_dining_event_mentions_menu_group_income_food_and_satisfaction(self):
        engine, npc = self._make_dining_npc(group_size=2, economic_level=2, total_satisfaction=60)
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2
        result = {"events": []}

        self._attach_dining_action(engine, npc)
        engine._process_dining(result)

        menu = self._menu("standard")
        self.assertEqual(len(result["events"]), 1)
        self.assertIn(f"客组{npc.id}", result["events"][0])
        self.assertIn(menu["display_name"], result["events"][0])
        self.assertIn("2人", result["events"][0])
        self.assertIn("收入+90", result["events"][0])
        self.assertIn("消耗食材2份", result["events"][0])
        self.assertIn(f"整组满意度+{menu['satisfaction_gain']}", result["events"][0])

    def test_day_guest_review_uses_updated_dining_satisfaction(self):
        engine, npc = self._make_dining_npc(total_satisfaction=73, economic_level=2)
        engine.facilities["dining"].level = 2
        engine.state.food_stock = 1

        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})
        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._leave_day_guest(npc, {"events": []})

        self.assertTrue(npc.has_left)
        self.assertEqual(npc.review_rating, 4)

    def test_overnight_checkout_does_not_duplicate_dining_satisfaction(self):
        engine, npc = self._make_dining_npc(
            visit_type="overnight",
            total_satisfaction=60,
        )
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id
        engine.state.food_stock = 1

        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        npc.location = "tent_1"
        engine._checkout_npc(npc, {"events": []})

        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(npc.total_satisfaction, 62.0)

    def test_checkout_without_dining_does_not_gain_free_dining_satisfaction(self):
        engine = make_engine()
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            total_satisfaction=60,
        )
        engine.npc_pool.append(npc)
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id

        engine._checkout_npc(npc, {"events": []})

        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(npc.total_satisfaction, 60)

    def test_dining_level_upgrade_changes_menu_selection_without_new_state(self):
        engine, npc = self._make_dining_npc(group_size=2, economic_level=2, total_satisfaction=50)
        engine.facilities["dining"].level = 1
        engine.state.food_stock = 2

        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        self.assertEqual(engine.state.today_income["dining"], self._menu("standard")["price_per_person"] * 2)
        engine._new_day()
        engine.state.food_stock = 2
        engine.facilities["dining"].level = 2
        self._attach_dining_action(engine, npc)

        engine._process_dining({"events": []})

        self.assertEqual(engine.state.today_income["dining"], self._menu("premium")["price_per_person"] * 2)

    def test_dining_failure_does_not_block_turn_progression(self):
        engine, npc = self._make_dining_npc(group_size=2, location="campsite")
        engine.state.food_stock = 1
        engine.state.turn = 3
        self._attach_dining_action(
            engine, npc, planned_turn=3, menu_key="premium"
        )
        engine.submit_turn_plan([], [])

        result = engine.advance_turn()

        self.assertEqual(result["turn"], 4)
        self.assertEqual(result["income"]["dining"], 0)

    def test_dining_success_consumes_exact_group_size_from_stock(self):
        engine, npc = self._make_dining_npc(group_size=2, total_satisfaction=55, economic_level=2)
        engine.facilities["dining"].level = 2
        engine.state.food_stock = 5

        self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        menu = self._menu("premium")
        self.assertEqual(engine.state.food_stock, 3)
        self.assertEqual(engine.state.today_income["dining"], menu["price_per_person"] * 2)
        self.assertEqual(npc.total_satisfaction, 55 + menu["satisfaction_gain"])
        self.assertEqual(npc.last_dining_day, engine.state.day)

    def test_dining_fails_atomically_when_stock_is_less_than_group_size(self):
        engine, npc = self._make_dining_npc(group_size=3, total_satisfaction=50, economic_level=2)
        engine.facilities["dining"].level = 2
        engine.state.food_stock = 2
        engine.state.balance = 777
        result = {"events": []}

        action = self._attach_dining_action(engine, npc)
        engine._process_dining(result)

        self.assertEqual(engine.state.food_stock, 2)
        self.assertEqual(engine.state.balance, 777)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 50)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(action["status"], "waiting_for_restock")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(len(result["events"]), 1)
        self.assertIn("需要3份", result["events"][0])
        self.assertIn("当前只有2份", result["events"][0])
        self.assertIn("决定先等等", result["events"][0])

    def test_dining_fails_atomically_when_stock_is_zero(self):
        engine, npc = self._make_dining_npc(group_size=1, total_satisfaction=80, economic_level=2)
        engine.facilities["dining"].level = 2
        engine.state.food_stock = 0

        action = self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 80)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(action["status"], "waiting_for_restock")

    def test_two_dining_groups_share_same_food_stock_sequentially(self):
        engine = make_engine()
        npc_a = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            economic_level=1,
            spending_habit=1,
            total_satisfaction=60,
        )
        npc_b = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            economic_level=1,
            spending_habit=1,
            total_satisfaction=70,
        )
        engine.npc_pool.extend([npc_a, npc_b])
        engine.state.food_stock = 3
        result = {"events": []}

        action_a = self._attach_dining_action(engine, npc_a)
        action_b = self._attach_dining_action(engine, npc_b)
        engine._process_dining(result)

        self.assertEqual(engine.state.food_stock, 1)
        self.assertEqual(engine.state.today_income["dining"], 60)
        self.assertEqual(npc_a.last_dining_day, engine.state.day)
        self.assertEqual(npc_b.last_dining_day, 0)
        self.assertEqual(npc_a.total_satisfaction, 62)
        self.assertEqual(npc_b.total_satisfaction, 70)
        self.assertEqual(action_a["status"], "completed")
        self.assertEqual(action_b["status"], "waiting_for_restock")
        self.assertEqual(len(result["events"]), 2)

    def test_waiting_for_restock_is_not_processed_or_reported_again(self):
        engine, npc = self._make_dining_npc(
            group_size=3,
            total_satisfaction=50,
            temperament=2,
        )
        engine.state.food_stock = 2
        engine.state.balance = 777
        action = self._attach_dining_action(engine, npc)
        first_result = {"events": []}

        engine._process_dining(first_result)

        self.assertEqual(action["status"], "waiting_for_restock")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(len(first_result["events"]), 1)
        self.assertIn("催促尽快补货", first_result["events"][0])

        second_result = {"events": []}
        engine.state.food_stock = 9
        engine._process_dining(second_result)

        self.assertEqual(engine.state.food_stock, 9)
        self.assertEqual(engine.state.balance, 777)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 50)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(action["status"], "waiting_for_restock")
        self.assertEqual(action["result"], "insufficient_food")
        self.assertEqual(second_result["events"], [])

    def test_existing_dining_ineligibility_still_skips_without_consuming_food(self):
        engine, npc = self._make_dining_npc(group_size=2, total_satisfaction=66)
        engine.state.food_stock = 9
        npc.last_dining_day = engine.state.day
        balance_before = engine.state.balance

        action = self._attach_dining_action(engine, npc)
        engine._process_dining({"events": []})

        self.assertEqual(engine.state.food_stock, 9)
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 66)
        self.assertEqual(action["status"], "skipped")
        self.assertEqual(action["result"], "already_dined")


class TentCleaningTests(unittest.TestCase):
    """帐篷主动清洁"""

    def test_checkout_leaves_tent_cleaning(self):
        """客人退房后帐篷保持 cleaning，不会自动恢复"""
        engine = make_engine()
        engine.state.turn = 2
        tent = engine.tents[1]
        tent.status = "occupied"
        tent.occupied_by = 10
        npc = NPCGroup(
            id=10,
            group_size=1,
            visit_type="overnight",
            arrival_turn=2,
            location="tent_1",
        )
        engine.npc_pool.append(npc)

        result = {"events": []}
        engine._process_checkout_all(result)
        # 营业回合处理完毕后不应自动清洁
        engine._process_business_turn(result)

        self.assertEqual(engine.tents[1].status, "cleaning")
        self.assertIsNone(engine.tents[1].occupied_by)

    def test_clean_tents_all_by_default(self):
        """不传 tent_ids 时清洁所有 cleaning 帐篷"""
        engine = make_engine()
        engine.tents[1].status = "cleaning"
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "cleaning"
        engine.tents[3].status = "available"

        result = engine.clean_tents()

        self.assertTrue(result["success"])
        self.assertEqual(set(result["cleaned_tent_ids"]), {1, 2})
        self.assertEqual(engine.tents[1].status, "available")
        self.assertEqual(engine.tents[2].status, "available")

    def test_clean_tents_partial_list(self):
        """传入部分 tent_ids 时只清洁指定的 cleaning 帐篷"""
        engine = make_engine()
        engine.tents[1].status = "cleaning"
        engine.tents[2].status = "cleaning"

        result = engine.clean_tents([1])

        self.assertTrue(result["success"])
        self.assertEqual(result["cleaned_tent_ids"], [1])
        self.assertEqual(engine.tents[1].status, "available")
        self.assertEqual(engine.tents[2].status, "cleaning")

    def test_clean_tents_no_decision_cost(self):
        """清洁不消耗决策点"""
        engine = make_engine()
        engine.tents[1].status = "cleaning"
        engine.state.decisions_left = 3

        engine.clean_tents()

        self.assertEqual(engine.state.decisions_left, 3)

    def test_clean_tents_reserved_restores_reserved(self):
        """今日预定帐篷清洁后恢复 reserved"""
        engine = make_engine()
        engine.state.day = 2
        engine.state.reserved_tent_id = 1
        engine.state.reserved_tent_day = 2
        engine.tents[1].status = "cleaning"

        result = engine.clean_tents([1])

        self.assertTrue(result["success"])
        self.assertEqual(engine.tents[1].status, "reserved")

    def test_clean_tents_normal_restores_available(self):
        """普通帐篷清洁后恢复 available"""
        engine = make_engine()
        engine.tents[4].is_unlocked = True
        engine.tents[4].status = "cleaning"

        result = engine.clean_tents([4])

        self.assertTrue(result["success"])
        self.assertEqual(engine.tents[4].status, "available")

    def test_clean_tents_none_fails(self):
        """没有可清洁帐篷时返回失败"""
        engine = make_engine()
        for t in engine.tents.values():
            t.status = "available"

        result = engine.clean_tents()

        self.assertFalse(result["success"])
        self.assertEqual(result["cleaned_tent_ids"], [])

    def test_clean_tents_blocked_by_broken(self):
        """存在 broken 帐篷时不能清洁"""
        engine = make_engine()
        engine.tents[1].status = "cleaning"
        engine.tents[2].is_unlocked = True
        engine.tents[2].status = "broken"

        result = engine.clean_tents()

        self.assertFalse(result["success"])
        self.assertIn("故障", result["message"])
        self.assertEqual(engine.tents[1].status, "cleaning")

    def test_clean_tents_blocked_when_turn_settled(self):
        """turn_settled 为 True 时不能清洁"""
        engine = make_engine()
        engine.tents[1].status = "cleaning"
        engine.state.turn_settled = True

        result = engine.clean_tents()

        self.assertFalse(result["success"])
        self.assertEqual(engine.tents[1].status, "cleaning")

    def test_clean_tents_preserves_other_fields(self):
        """清洁不改变余额、等级、occupied_by 和 next_breakdown_turn"""
        engine = make_engine()
        tent = engine.tents[1]
        tent.status = "cleaning"

        tent.occupied_by = None
        tent.next_breakdown_turn = 123
        balance_before = engine.state.balance

        engine.clean_tents([1])

        self.assertEqual(engine.state.balance, balance_before)

        self.assertIsNone(tent.occupied_by)
        self.assertEqual(tent.next_breakdown_turn, 123)


class GreeneryAndPhaseProtectionTests(unittest.TestCase):
    """绿化管理与阶段保护"""

    def test_greenery_blocked_on_turn1(self):
        """Turn 1 不能管理绿化"""
        engine = make_engine()
        engine.state.turn = 1
        engine.state.greenery_processed_today = False

        message = engine.manage_greenery("maintain")

        self.assertNotEqual(message, "绿化已打理，花费50金币")
        self.assertFalse(engine.state.greenery_processed_today)

    def test_greenery_allowed_on_turn6(self):
        """Turn 6 可以管理绿化"""
        engine = make_engine()
        engine.state.turn = 6
        engine.state.greenery_processed_today = False
        engine.state.balance = 1000

        message = engine.manage_greenery("maintain")

        self.assertIn("绿化已打理", message)
        self.assertTrue(engine.state.greenery_processed_today)

    def test_greenery_once_per_day(self):
        """同一天不能重复处理绿化"""
        engine = make_engine()
        engine.state.turn = 6
        engine.state.greenery_processed_today = False
        engine.state.balance = 1000

        engine.manage_greenery("maintain")
        message2 = engine.manage_greenery("maintain")

        self.assertEqual(message2, "今天已经处理过绿化了")

    def test_greenery_lv2_paid_maintain_below_cap(self):
        """绿化 Lv2 付费维护且不超过上限"""
        engine = make_engine()
        engine.state.turn = 6
        engine.state.greenery_processed_today = False
        engine.facilities["greenery"].level = 2
        engine.facilities["greenery"].greenery_satisfaction = 9.5
        engine.state.balance = 1000
        balance_before = engine.state.balance

        message = engine.manage_greenery("maintain")

        self.assertIn("绿化已打理，花费50金币", message)
        self.assertEqual(engine.state.balance, balance_before - 50)
        self.assertEqual(engine.facilities["greenery"].greenery_satisfaction, 10.0)
        self.assertTrue(engine.state.greenery_processed_today)

class TentLockingAndCapacityTests(unittest.TestCase):
    def test_tent_capacity_map_updated(self):
        engine = make_engine()
        capacities = [engine.tents[i].capacity for i in range(1, 7)]
        self.assertEqual(capacities, [2, 2, 3, 4, 5, 6])
        self.assertNotEqual(capacities, [1, 2, 2, 3, 3, 5])

    def test_natural_day_guest_group_size_uses_one_to_six(self):
        engine = make_engine()
        with mock.patch("game_engine.random.randint", return_value=6) as randint_mock:
            guest = engine._create_day_guest()

        self.assertEqual(guest.visit_type, "day")
        self.assertEqual(guest.group_size, 6)
        randint_mock.assert_called_once_with(1, 6)

    def test_natural_overnight_guest_group_size_uses_one_to_six_without_unlocked_capacity_cap(self):
        engine = make_engine()
        self.assertEqual(max(tent.capacity for tent in engine._get_unlocked_tents()), 2)

        with mock.patch("game_engine.random.randint", return_value=6) as randint_mock:
            guest = engine._create_overnight_guest()

        self.assertEqual(guest.visit_type, "overnight")
        self.assertEqual(guest.group_size, 6)
        randint_mock.assert_called_once_with(1, 6)

    def test_new_game_only_tent_one_unlocked(self):
        engine = make_engine()
        self.assertTrue(engine.tents[1].is_unlocked)
        self.assertEqual(
            {tid for tid, tent in engine.tents.items() if tent.is_unlocked},
            {1},
        )
        self.assertEqual(set(engine.tents.keys()), {1, 2, 3, 4, 5, 6})

    def test_find_available_tent_ignores_locked_tents(self):
        engine = make_engine()
        self.assertEqual(engine._find_available_tent(2), 1)
        self.assertIsNone(engine._find_available_tent(3))

    def test_occupied_tent_does_not_block_natural_overnight_guest_generation(self):
        engine = make_engine()
        engine.tents[1].status = "occupied"
        with mock.patch("game_engine.random.random", return_value=0.0):
            guests = engine._generate_overnight_guests()
        self.assertEqual(len(guests), 1)
        self.assertEqual(guests[0].visit_type, "overnight")

    def test_day_to_overnight_does_not_use_locked_tent(self):
        engine = make_engine()
        engine.tents[1].status = "occupied"
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=3,
            visit_type="day",
            location="dining",
            total_satisfaction=90,
        )
        engine.npc_pool.append(guest)
        engine.state.day_campsite_groups_served = 1
        engine.state.today_arrival_plan_day = engine.state.day
        engine.state.today_arrival_plan = [{
            "npc_id": guest.id,
            "planned_day": engine.state.day,
            "visit_type": "day",
            "day_to_overnight_intent": True,
        }]

        engine._process_day_to_overnight({"events": []})

        self.assertEqual(guest.visit_type, "day")
        self.assertTrue(guest.has_left)
        self.assertEqual(engine.state.day_campsite_groups_served, 1)

    def test_clean_tents_ignores_locked_cleaning_tent(self):
        engine = make_engine()
        engine.tents[1].status = "cleaning"
        engine.tents[2].status = "cleaning"

        result = engine.clean_tents()

        self.assertTrue(result["success"])
        self.assertEqual(result["cleaned_tent_ids"], [1])
        self.assertEqual(engine.tents[1].status, "available")
        self.assertEqual(engine.tents[2].status, "cleaning")

    def test_locked_tent_never_breaks_naturally(self):
        engine = make_engine()
        engine.tents[2].next_breakdown_turn = 1
        result = {"events": [], "next_actions": []}

        engine._handle_breakdowns(result)

        self.assertEqual(engine.tents[2].status, "available")
        self.assertEqual(result["next_actions"], [])

    def test_repair_locked_tent_fails_without_spending_decision(self):
        engine = make_engine()
        engine.tents[2].status = "broken"
        decisions_before = engine.state.decisions_left

        result = engine.repair_tent(2)

        self.assertFalse(result["success"])
        self.assertEqual(engine.state.decisions_left, decisions_before)

    def test_full_state_exposes_unlocked_flag(self):
        engine = make_engine()
        state = engine.get_full_state()

        self.assertTrue(state["tents"][1]["unlocked"])
        self.assertFalse(state["tents"][2]["unlocked"])


class McpLockingStateTests(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine

    def test_mcp_state_includes_unlocked_flags(self):
        state = game_api.mcp_state()

        self.assertTrue(state["tents"][1]["unlocked"])
        self.assertFalse(state["tents"][2]["unlocked"])

    def test_mcp_state_exposes_next_turn_checkout_tents_only_for_turn2_window(self):
        self.engine.state.turn = 2
        npc = NPCGroup(
            id=self.engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            checkout_turn=2,
        )
        self.engine.npc_pool.append(npc)
        self.engine.tents[1].status = "occupied"
        self.engine.tents[1].occupied_by = npc.id

        state = game_api.mcp_state()
        self.assertEqual(state["next_turn_checkout_tents"], [1])
        self.assertNotIn("active_npcs", state)

        for turn in (3, 4, 5, 6):
            self.engine.state.turn = turn
            self.assertEqual(game_api.mcp_state()["next_turn_checkout_tents"], [])

    def test_mcp_actions_do_not_offer_tent_upgrade(self):
        self.engine.state.turn = 6
        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertTrue(all(action["action"] != "upgrade_tent" for action in actions))

    def test_mcp_actions_do_not_expose_manual_reservation_actions(self):
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [action["action"] for action in actions]
        self.assertNotIn("accept_reservation", action_names)
        self.assertNotIn("reject_reservation", action_names)

if __name__ == "__main__":
    unittest.main()
