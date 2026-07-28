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
    td = tempfile.TemporaryDirectory()
    _TEMP_DIRS.append(td)
    return CampingPlazaEngine(db_path=os.path.join(td.name, "test.db"))


def tearDownModule():
    for td in _TEMP_DIRS:
        td.cleanup()
    _TEMP_DIRS.clear()


class Turn4OrderTests(unittest.TestCase):
    """Turn 4 日间客转过夜执行顺序"""

    def _engine_at_turn4(self):
        engine = make_engine()
        engine.state.day = 1
        engine.state.turn = 4
        # 屏蔽故障干扰
        for t in engine.tents.values():
            t.next_breakdown_turn = 99999
        return engine

    def test_existing_day_guest_converts(self):
        """Turn 4 开始前已存在的高满意度日间客参与转过夜"""
        engine = self._engine_at_turn4()
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            arrival_turn=3,
            location="dining",
            total_satisfaction=80,
        )
        engine.npc_pool.append(guest)

        result = {"events": []}
        engine._process_business_turn(result)

        self.assertEqual(guest.visit_type, "overnight")
        self.assertTrue(guest.location.startswith("tent_"))
        self.assertTrue(
            any("日间游客转为过夜" in e for e in engine.state.day_to_overnight_cache)
        )

    def test_new_day_guest_not_processed_same_turn(self):
        """Turn 4 当回合新生成的日间客不被同一回合处理"""
        engine = self._engine_at_turn4()
        new_guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            arrival_turn=0,
            location="dining",
            total_satisfaction=80,
        )
        with mock.patch.object(
            CampingPlazaEngine, "_generate_day_guests", return_value=[new_guest]
        ):
            with mock.patch.object(
                CampingPlazaEngine, "_generate_overnight_guests", return_value=[]
            ):
                result = {"events": []}
                engine._process_business_turn(result)

        self.assertEqual(new_guest.visit_type, "day")
        self.assertFalse(new_guest.has_left)
        self.assertIn(new_guest.location, ("dining", "entertainment"))

    def test_cache_flushed_on_turn5(self):
        """转过夜事件写入缓存，Turn 5 展示后清空"""
        engine = self._engine_at_turn4()
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            arrival_turn=3,
            location="entertainment",
            total_satisfaction=80,
        )
        engine.npc_pool.append(guest)

        result4 = {"events": []}
        engine._process_business_turn(result4)
        cache = list(engine.state.day_to_overnight_cache)
        self.assertGreater(len(cache), 0)
        self.assertFalse(
            any("日间游客转为过夜" in e for e in result4["events"])
        )

        engine.state.turn = 5
        result5 = {"events": []}
        engine._process_business_turn(result5)

        for event in cache:
            self.assertIn(event, result5["events"])
        self.assertEqual(len(engine.state.day_to_overnight_cache), 0)


class DayCampsiteCapacityTests(unittest.TestCase):
    """日间营位每日10组上限与生命周期"""

    def test_new_game_counter_starts_at_zero(self):
        engine = make_engine()

        self.assertEqual(engine.state.day_campsite_groups_served, 0)
        self.assertEqual(engine.get_day_campsite_remaining(), 10)

    def test_successful_day_guest_checkin_increments_counter_by_group(self):
        engine = make_engine()
        engine.state.turn = 2

        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=3,
            visit_type="day",
        )
        with mock.patch.object(CampingPlazaEngine, "_generate_day_guests", return_value=[guest]):
            with mock.patch.object(CampingPlazaEngine, "_generate_overnight_guests", return_value=[]):
                with mock.patch("game_engine.random.random", return_value=0.0):
                    result = {"events": []}
                    engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 1)
        self.assertEqual(engine.get_day_campsite_remaining(), 9)
        self.assertEqual(engine.state.today_income["campsite"], engine.CAMPSITE_FEE)
        self.assertEqual(engine.state.balance, 1000 + engine.CAMPSITE_FEE)
        self.assertEqual(len(engine.npc_pool), 1)

    def test_counter_accumulates_across_business_turns_up_to_ten(self):
        engine = make_engine()
        engine.state.day_campsite_groups_served = 0

        with mock.patch("game_engine.random.randint", return_value=4):
            guests_turn2 = engine._generate_day_guests()
            engine.state.day_campsite_groups_served += len(guests_turn2)
            guests_turn3 = engine._generate_day_guests()
            engine.state.day_campsite_groups_served += len(guests_turn3)
            guests_turn4 = engine._generate_day_guests()

        self.assertEqual(len(guests_turn2), 4)
        self.assertEqual(len(guests_turn3), 4)
        self.assertEqual(len(guests_turn4), 2)
        self.assertEqual(engine.state.day_campsite_groups_served + len(guests_turn4), 10)

    def test_remaining_two_slots_caps_generation_and_revenue(self):
        engine = make_engine()
        engine.state.turn = 3
        engine.state.day_campsite_groups_served = 8

        with mock.patch.object(CampingPlazaEngine, "_generate_overnight_guests", return_value=[]):
            with mock.patch("game_engine.random.randint", side_effect=[5, 2, 3]):
                with mock.patch("game_engine.random.choices", side_effect=[[1], [1], [1], [1], [1], [1]]):
                    with mock.patch("game_engine.random.random", return_value=0.0):
                        result = {"events": []}
                        engine._process_checkin(result)

        day_guests = [n for n in engine.npc_pool if n.visit_type == "day"]
        self.assertEqual(len(day_guests), 2)
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
        overnight_guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
        )

        with mock.patch.object(CampingPlazaEngine, "_generate_day_guests", return_value=[]):
            with mock.patch.object(CampingPlazaEngine, "_generate_overnight_guests", return_value=[overnight_guest]):
                result = {"events": []}
                engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 3)
        self.assertEqual(engine.state.today_income["campsite"], 0)

    def test_reserved_overnight_guest_does_not_increase_day_counter(self):
        engine = make_engine()
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.day_campsite_groups_served = 4
        engine.state.reservation = {
            "group_size": 1,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        engine.state.reserved_tent_id = 1
        engine.state.reserved_tent_day = 2
        engine.tents[1].status = "reserved"

        result = {"events": []}
        engine._process_reservations(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 4)
        reserved_npcs = [n for n in engine.npc_pool if n.is_reserved]
        self.assertEqual(len(reserved_npcs), 1)

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

        engine._process_day_to_overnight({"events": []})

        self.assertEqual(engine.state.day_campsite_groups_served, 5)
        self.assertEqual(guest.visit_type, "overnight")

    def test_turn4_still_accepts_new_day_guests(self):
        engine = make_engine()
        engine.state.turn = 4
        engine.state.day_campsite_groups_served = 9
        new_guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
        )

        with mock.patch.object(CampingPlazaEngine, "_generate_day_guests", return_value=[new_guest]):
            with mock.patch.object(CampingPlazaEngine, "_generate_overnight_guests", return_value=[]):
                with mock.patch("game_engine.random.random", return_value=0.0):
                    result = {"events": []}
                    engine._process_checkin(result)

        self.assertEqual(engine.state.day_campsite_groups_served, 10)
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "day"]), 1)

    def test_turn5_departs_all_remaining_day_guests_but_keeps_overnight(self):
        engine = make_engine()
        engine.state.turn = 5
        day_guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            location="dining",
            total_satisfaction=65,
        )
        overnight_guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            total_satisfaction=80,
        )
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = overnight_guest.id
        engine.npc_pool.extend([day_guest, overnight_guest])
        engine.submit_turn_plan([], [])

        with mock.patch("game_engine.random.random", return_value=0.99):
            result = engine.advance_turn()

        self.assertEqual(result["turn"], 6)
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "day" and not n.has_left]), 0)
        self.assertEqual(len([n for n in engine.npc_pool if n.visit_type == "overnight" and not n.has_left]), 1)
        self.assertGreaterEqual(len(engine.npc_history), 1)

    def test_new_day_resets_day_campsite_counter_once(self):
        engine = make_engine()
        engine.state.day = 3
        engine.state.turn = 6
        engine.state.day_campsite_groups_served = 10

        engine._new_day()

        self.assertEqual(engine.state.day, 4)
        self.assertEqual(engine.state.turn, 1)
        self.assertEqual(engine.state.day_campsite_groups_served, 0)

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


class ReservationProtectionTests(unittest.TestCase):
    """预定系统保护规则"""

    def _make_pending_reservation(self, engine):
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }

    def test_broken_tent_blocks_accept(self):
        """broken 帐篷存在时不能接受预定"""
        engine = make_engine()
        engine.tents[1].status = "broken"
        self._make_pending_reservation(engine)

        result = engine.accept_reservation(2)

        self.assertFalse(result["success"])
        self.assertIn("故障", result["message"])

    def test_broken_tent_blocks_reject(self):
        """broken 帐篷存在时不能拒绝预定"""
        engine = make_engine()
        engine.tents[1].status = "broken"
        self._make_pending_reservation(engine)

        result = engine.reject_reservation()

        self.assertFalse(result["success"])
        self.assertIn("故障", result["message"])

    def test_turn6_blocks_accept(self):
        """Turn 6 不能接受预定"""
        engine = make_engine()
        engine.state.turn = 6
        self._make_pending_reservation(engine)

        result = engine.accept_reservation(2)

        self.assertFalse(result["success"])
        self.assertIn("营业回合", result["message"])

    def test_turn6_blocks_reject(self):
        """Turn 6 不能拒绝预定"""
        engine = make_engine()
        engine.state.turn = 6
        self._make_pending_reservation(engine)

        result = engine.reject_reservation()

        self.assertFalse(result["success"])
        self.assertIn("营业回合", result["message"])

    def test_accepted_reservation_cannot_reject(self):
        """已接受预定不能再次拒绝"""
        engine = make_engine()
        self._make_pending_reservation(engine)
        engine.accept_reservation(2)

        result = engine.reject_reservation()

        self.assertFalse(result["success"])
        self.assertIn("已接受", result["message"])

    def test_reject_without_reservation_no_complaint(self):
        """没有预定请求时拒绝不会触发随机抱怨"""
        engine = make_engine()
        engine.state.reservation = None
        # 即使 random < 0.3 也不应抱怨
        with mock.patch("game_engine.random.random", return_value=0.1):
            result = engine.reject_reservation()

        self.assertFalse(result["success"])
        self.assertEqual(len(engine.state.today_events), 0)

    def test_accept_no_suitable_tent_records_complaint(self):
        """容量不足且随机值小于0.3时，接受预定失败并写入抱怨"""
        engine = make_engine()
        self._make_pending_reservation(engine)
        # 让所有帐篷容量都不足
        for t in engine.tents.values():
            t.capacity = 1
        balance_before = engine.state.balance
        accommodation_before = engine.state.today_income["accommodation"]
        reserved_id_before = engine.state.reserved_tent_id
        reserved_day_before = engine.state.reserved_tent_day

        with mock.patch("game_engine.random.random", return_value=0.1):
            result = engine.accept_reservation(2)

        self.assertFalse(result["success"])
        self.assertIn("没有容量合适的帐篷", result["message"])
        self.assertEqual(len(engine.state.today_events), 1)
        self.assertIn("不太满意的帖子", engine.state.today_events[0])
        self.assertIsNotNone(engine.state.reservation)
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.today_income["accommodation"], accommodation_before)
        self.assertEqual(engine.state.reserved_tent_id, reserved_id_before)
        self.assertEqual(engine.state.reserved_tent_day, reserved_day_before)

    def test_accept_no_suitable_tent_no_complaint(self):
        """容量不足且随机值大于等于0.3时，不写抱怨事件"""
        engine = make_engine()
        self._make_pending_reservation(engine)
        for t in engine.tents.values():
            t.capacity = 1

        with mock.patch("game_engine.random.random", return_value=0.5):
            result = engine.accept_reservation(2)

        self.assertFalse(result["success"])
        self.assertEqual(len(engine.state.today_events), 0)
        self.assertIsNotNone(engine.state.reservation)

    def test_reject_reservation_uses_shared_recorder(self):
        """主动拒绝预定使用同一抱怨判定并清空待处理请求"""
        engine = make_engine()
        self._make_pending_reservation(engine)

        with mock.patch("game_engine.random.random", return_value=0.1):
            result = engine.reject_reservation()

        self.assertTrue(result["success"])
        self.assertIsNone(engine.state.reservation)
        self.assertEqual(len(engine.state.today_events), 1)
        self.assertIn("不太满意的帖子", engine.state.today_events[0])

    def test_accept_charges_once(self):
        """接受预定立即收取住宿费，入住时不重复收费"""
        engine = make_engine()
        self._make_pending_reservation(engine)
        balance_before = engine.state.balance
        accommodation_before = engine.state.today_income["accommodation"]

        result = engine.accept_reservation(2)

        self.assertTrue(result["success"])
        payment = result["payment"]
        self.assertEqual(engine.state.balance, balance_before + payment)
        self.assertEqual(
            engine.state.today_income["accommodation"],
            accommodation_before + payment,
        )

        # 模拟第二天预定客入住
        engine.state.day = 2
        engine.state.reserved_tent_day = 2
        engine.tents[engine.state.reserved_tent_id].status = "reserved"
        result_checkin = {"events": []}
        engine._process_reservations(result_checkin)

        # 余额和住宿收入不应再次增加
        self.assertEqual(engine.state.balance, balance_before + payment)
        self.assertEqual(
            engine.state.today_income["accommodation"],
            accommodation_before + payment,
        )


class HiddenInfoTests(unittest.TestCase):
    """对外状态隐藏内部字段"""

    def test_tents_hide_internal_fields(self):
        """tents 不含 next_breakdown_turn 和 satisfaction_bonus"""
        engine = make_engine()
        state = engine.get_full_state()

        for tid, tent in state["tents"].items():
            self.assertNotIn("next_breakdown_turn", tent)
            self.assertNotIn("satisfaction_bonus", tent)

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

        with mock.patch("game_engine.random.random", return_value=0.8):
            engine._process_reservations({"events": []})

        reserved_npcs = [n for n in engine.npc_pool if n.is_reserved]
        self.assertEqual(len(reserved_npcs), 1)
        self.assertEqual(reserved_npcs[0].checkout_turn, 2)

    def test_day_to_overnight_assigns_checkout_turn(self):
        engine = make_engine()
        guest = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="dining",
            total_satisfaction=90,
        )
        engine.npc_pool.append(guest)

        with mock.patch("game_engine.random.random", return_value=0.2):
            engine._process_day_to_overnight({"events": []})

        self.assertEqual(guest.visit_type, "overnight")
        self.assertEqual(guest.checkout_turn, 1)

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

    def test_dining_entertainment_income_multipliers(self):
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
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
        )
        for key, value in overrides.items():
            setattr(npc, key, value)
        engine.npc_pool.append(npc)
        return engine, npc

    def test_new_npc_has_not_consumed_dining_today(self):
        engine, npc = self._make_dining_npc()

        self.assertEqual(npc.last_dining_day, 0)
        self.assertFalse(engine._has_consumed_dining_today(npc))

    def test_successful_dining_marks_day_and_adds_satisfaction(self):
        engine, npc = self._make_dining_npc(group_size=2, total_satisfaction=60)
        engine.state.food_stock = 2
        result = {"events": []}

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining(result)

        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.today_income["dining"], 60)
        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(npc.total_satisfaction, 65.0)
        self.assertEqual(len(result["events"]), 1)

    def test_same_day_repeat_does_not_charge_twice_or_repeat_satisfaction(self):
        engine, npc = self._make_dining_npc(group_size=3, total_satisfaction=70)
        engine.state.food_stock = 6
        result1 = {"events": []}
        result2 = {"events": []}

        with mock.patch("game_engine.random.random", return_value=0.0):
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
        engine, npc = self._make_dining_npc(total_satisfaction=60)
        engine.state.food_stock = 2

        with mock.patch("game_engine.random.random", return_value=0.99):
            engine._process_dining({"events": []})

        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 60)

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.today_income["dining"], 30)
        self.assertEqual(npc.total_satisfaction, 65.0)

    def test_next_day_can_consume_again_without_manual_reset(self):
        engine, npc = self._make_dining_npc()
        engine.state.food_stock = 2

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        first_day_income = engine.state.today_income["dining"]
        engine._new_day()

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(engine.state.today_income["dining"], first_day_income)

    def test_dining_revenue_scales_with_group_size(self):
        for group_size, expected in ((1, 30), (2, 60), (3, 90)):
            engine, _npc = self._make_dining_npc(group_size=group_size)
            engine.state.food_stock = group_size
            with self.subTest(group_size=group_size):
                with mock.patch("game_engine.random.random", return_value=0.0):
                    engine._process_dining({"events": []})
                self.assertEqual(engine.state.today_income["dining"], expected)

    def test_dining_uses_economic_level_and_multiplier_once_before_group_multiplier(self):
        engine, npc = self._make_dining_npc(group_size=2, economic_level=2)
        engine.facilities["dining"].dining_income_multiplier = 2.0
        engine.state.food_stock = 2

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(engine._get_dining_unit_revenue(npc), 72)
        self.assertEqual(engine.state.today_income["dining"], 144)

    def test_dining_event_mentions_group_size_and_total_income_once(self):
        engine, _npc = self._make_dining_npc(group_size=2)
        engine.state.food_stock = 2
        result = {"events": []}

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining(result)

        self.assertEqual(len(result["events"]), 1)
        self.assertIn("2人", result["events"][0])
        self.assertIn("收入+60", result["events"][0])

    def test_day_guest_review_uses_updated_dining_satisfaction(self):
        engine, npc = self._make_dining_npc(total_satisfaction=70)
        engine.state.food_stock = 1

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})
            engine._leave_day_guest(npc)

        self.assertTrue(npc.has_left)
        self.assertEqual(npc.review_rating, 4)

    def test_overnight_checkout_does_not_duplicate_dining_satisfaction(self):
        engine, npc = self._make_dining_npc(
            visit_type="overnight",
            total_satisfaction=60,
            location="dining",
        )
        engine.tents[1].status = "occupied"
        engine.tents[1].occupied_by = npc.id
        engine.state.food_stock = 1

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        npc.location = "tent_1"
        engine._checkout_npc(npc, {"events": []})

        self.assertEqual(npc.last_dining_day, engine.state.day)
        self.assertEqual(npc.total_satisfaction, 68.0)

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
        self.assertEqual(npc.total_satisfaction, 63.0)

    def test_upgraded_dining_satisfaction_applies_on_success(self):
        engine, npc = self._make_dining_npc(total_satisfaction=50)
        engine.facilities["dining"].dining_satisfaction = 9.0
        engine.state.food_stock = 1

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(npc.total_satisfaction, 59.0)

    def test_temperament_is_not_used_for_dining_amount(self):
        engine_a, npc_a = self._make_dining_npc(temperament=0)
        engine_b, npc_b = self._make_dining_npc(temperament=2)

        self.assertEqual(
            engine_a._get_dining_unit_revenue(npc_a),
            engine_b._get_dining_unit_revenue(npc_b)
        )

    def test_turn5_day_guest_departure_still_happens_after_dining(self):
        engine, npc = self._make_dining_npc(group_size=1, total_satisfaction=70)
        engine.state.turn = 5
        engine.state.food_stock = 1
        engine.submit_turn_plan([], [])

        with mock.patch("game_engine.random.random", return_value=0.0):
            result = engine.advance_turn()

        self.assertTrue(npc.has_left)
        self.assertEqual(npc.last_dining_day, 1)
        self.assertEqual(result["income"]["dining"], 30)

    def test_dining_failure_does_not_block_turn_progression(self):
        engine, _npc = self._make_dining_npc()
        engine.state.food_stock = 1
        engine.state.turn = 3
        engine.submit_turn_plan([], [])

        with mock.patch.object(CampingPlazaEngine, "_generate_day_guests", return_value=[]):
            with mock.patch.object(CampingPlazaEngine, "_generate_overnight_guests", return_value=[]):
                with mock.patch("game_engine.random.random", side_effect=[0.99, 0.99]):
                    result = engine.advance_turn()

        self.assertEqual(result["turn"], 4)
        self.assertEqual(result["income"]["dining"], 0)

    def test_dining_success_consumes_exact_group_size_from_stock(self):
        engine, npc = self._make_dining_npc(group_size=2, total_satisfaction=55)
        engine.state.food_stock = 5

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(engine.state.food_stock, 3)
        self.assertEqual(engine.state.today_income["dining"], 60)
        self.assertEqual(npc.total_satisfaction, 60.0)
        self.assertEqual(npc.last_dining_day, engine.state.day)

    def test_dining_fails_atomically_when_stock_is_less_than_group_size(self):
        engine, npc = self._make_dining_npc(group_size=3, total_satisfaction=50)
        engine.state.food_stock = 2
        engine.state.balance = 777
        result = {"events": []}

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining(result)

        self.assertEqual(engine.state.food_stock, 2)
        self.assertEqual(engine.state.balance, 777)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 50)
        self.assertEqual(npc.last_dining_day, 0)
        self.assertEqual(len(result["events"]), 1)
        self.assertIn("需要3份", result["events"][0])
        self.assertIn("当前只有2份", result["events"][0])

    def test_dining_fails_atomically_when_stock_is_zero(self):
        engine, npc = self._make_dining_npc(group_size=1, total_satisfaction=80)
        engine.state.food_stock = 0

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(engine.state.food_stock, 0)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 80)
        self.assertEqual(npc.last_dining_day, 0)

    def test_two_dining_groups_share_same_food_stock_sequentially(self):
        engine = make_engine()
        npc_a = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
            total_satisfaction=60,
        )
        npc_b = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
            total_satisfaction=70,
        )
        engine.npc_pool.extend([npc_a, npc_b])
        engine.state.food_stock = 3
        result = {"events": []}

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining(result)

        self.assertEqual(engine.state.food_stock, 1)
        self.assertEqual(engine.state.today_income["dining"], 60)
        self.assertEqual(npc_a.last_dining_day, engine.state.day)
        self.assertEqual(npc_b.last_dining_day, 0)
        self.assertEqual(npc_a.total_satisfaction, 65.0)
        self.assertEqual(npc_b.total_satisfaction, 70)
        self.assertEqual(len(result["events"]), 2)

    def test_existing_dining_ineligibility_still_skips_without_consuming_food(self):
        engine, npc = self._make_dining_npc(group_size=2, total_satisfaction=66)
        engine.state.food_stock = 9
        npc.last_dining_day = engine.state.day
        balance_before = engine.state.balance

        with mock.patch("game_engine.random.random", return_value=0.0):
            engine._process_dining({"events": []})

        self.assertEqual(engine.state.food_stock, 9)
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.today_income["dining"], 0)
        self.assertEqual(npc.total_satisfaction, 66)


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
        tent.level = 2
        tent.occupied_by = None
        tent.next_breakdown_turn = 123
        balance_before = engine.state.balance

        engine.clean_tents([1])

        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(tent.level, 2)
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

    def test_greenery_lv2_auto_maintain_free(self):
        """绿化 Lv2 自动维护且不扣维护费"""
        engine = make_engine()
        engine.state.turn = 6
        engine.state.greenery_processed_today = False
        engine.facilities["greenery"].level = 2
        balance_before = engine.state.balance

        message = engine.manage_greenery("maintain")

        self.assertIn("自动维护", message)
        self.assertEqual(engine.state.balance, balance_before)

class TentLockingAndCapacityTests(unittest.TestCase):
    def test_tent_capacity_map_updated(self):
        engine = make_engine()
        capacities = [engine.tents[i].capacity for i in range(1, 7)]
        self.assertEqual(capacities, [2, 2, 3, 3, 4, 5])
        self.assertNotEqual(capacities, [1, 2, 2, 3, 3, 5])

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

    def test_locked_tent_not_used_for_direct_overnight_guests(self):
        engine = make_engine()
        engine.tents[1].status = "occupied"
        with mock.patch("game_engine.random.random", return_value=0.0):
            guests = engine._generate_overnight_guests()
        self.assertEqual(guests, [])

    def test_accept_reservation_fails_when_only_locked_tent_has_capacity(self):
        engine = make_engine()
        engine.state.reservation = {
            "group_size": 3,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        balance_before = engine.state.balance
        decisions_before = engine.state.decisions_left

        with mock.patch("game_engine.random.random", return_value=0.9):
            result = engine.accept_reservation(3)

        self.assertFalse(result["success"])
        self.assertEqual(engine.state.balance, balance_before)
        self.assertEqual(engine.state.decisions_left, decisions_before)
        self.assertIsNone(engine.state.reserved_tent_id)
        self.assertIsNotNone(engine.state.reservation)

    def test_accept_reservation_uses_unlocked_tent(self):
        engine = make_engine()
        engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }

        result = engine.accept_reservation(2)

        self.assertTrue(result["success"])
        self.assertEqual(engine.state.reserved_tent_id, 1)

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

    def test_upgrade_locked_tent_fails_without_spending_balance(self):
        engine = make_engine()
        engine.state.turn = 6
        balance_before = engine.state.balance

        result = engine.upgrade_tent(2)

        self.assertFalse(result["success"])
        self.assertEqual(engine.state.balance, balance_before)

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

    def test_mcp_actions_do_not_offer_locked_tent_upgrade(self):
        self.engine.state.turn = 6

        actions = game_api.mcp_available_actions()["available_actions"]
        upgrade_tent_ids = [
            action["params"]["tent_id"]
            for action in actions
            if action["action"] == "upgrade_tent"
        ]

        self.assertEqual(upgrade_tent_ids, [1])
        self.assertNotIn("unlock_tent", [action["action"] for action in actions])

    def test_mcp_actions_reservation_capacity_ignores_locked_tents(self):
        self.engine.state.turn = 3
        self.engine.state.reservation = {
            "group_size": 3,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }

        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [action["action"] for action in actions]

        self.assertNotIn("accept_reservation", action_names)
        self.assertIn("reject_reservation", action_names)


if __name__ == "__main__":
    unittest.main()
