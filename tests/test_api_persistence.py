"""露营广场 API 持久化回归测试

验证 game_api.py 的写入口在执行后都会触发 engine.save_state()，
并且关键副作用可通过同一数据库恢复。

仅使用 Python 标准库 unittest/tempfile/unittest.mock；
不启动 FastAPI 服务，直接调用 game_api 中函数；
所有测试使用临时目录数据库，不触碰正式 camping_plaza.db。
"""

import json
import os
import sqlite3
import sys
import unittest
from unittest import mock

# 将 camping_plaza 包加入路径（不依赖 __init__.py，Python 3 命名空间包）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class ApiPersistenceTestCase(unittest.TestCase):
    """公共基类：每个测试独立临时数据库与 game_api.engine"""

    def setUp(self):
        self.db_path = os.path.join(
            _PROJECT_ROOT,
            f".test_api_persistence_{self._testMethodName}.sqlite",
        )
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
        self.addCleanup(self._cleanup_db)

        # 为当前测试创建独立引擎，并替换 game_api 全局 engine
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self._original_engine = game_api.engine
        game_api.engine = self.engine

        # 屏蔽随机故障干扰，避免非预期 broken 破坏测试状态
        for tent in self.engine.tents.values():
            tent.next_breakdown_turn = 999999

    def tearDown(self):
        game_api.engine = self._original_engine

    def _cleanup_db(self):
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except OSError:
                pass

    def _new_engine_from_db(self):
        """用同一数据库路径新建引擎，模拟服务重启后恢复"""
        return CampingPlazaEngine(db_path=self.db_path)

    def _action(self, action, params=None):
        """构造 ActionRequest 并调用 game_api.do_action"""
        return game_api.do_action(game_api.ActionRequest(action=action, params=params))

    def _plan(self, free_actions=None, actions=None):
        """构造 TurnPlanRequest 并调用 game_api.submit_turn_plan"""
        return game_api.submit_turn_plan(
            game_api.TurnPlanRequest(
                free_actions=free_actions or [],
                actions=actions or [],
            )
        )


class SaveStateCalledTests(ApiPersistenceTestCase):
    """验证写入口执行后都会调用 engine.save_state()"""

    def test_advance_turn_saves(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.advance_turn()
            save_mock.assert_called_once()

    def test_submit_turn_plan_saves(self):
        self.engine.state.turn = 2
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._plan()
            save_mock.assert_called_once()

    def test_repair_tent_saves(self):
        self.engine.tents[1].status = "broken"
        self.engine.state.decisions_left = 2
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("repair_tent", {"tent_id": 1})
            save_mock.assert_called_once()

    def test_clean_tents_saves(self):
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[2].status = "cleaning"
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("clean_tents", {"tent_ids": [1, 2]})
            save_mock.assert_called_once()

    def test_upgrade_facility_saves(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("upgrade_facility", {"facility_name": "dining"})
            save_mock.assert_called_once()

    def test_improve_service_saves(self):
        self.engine.state.turn = 2
        self.engine.state.decisions_left = 3
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("improve_service")
            save_mock.assert_called_once()

    def test_manage_greenery_saves(self):
        self.engine.state.turn = 6
        self.engine.facilities["greenery"].level = 1
        self.engine.state.balance = 99999
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("manage_greenery", {"action": "maintain"})
            save_mock.assert_called_once()

    def test_buy_food_package_saves(self):
        self.engine.state.turn = 6
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("buy_food_package", {"package_key": "small"})
            save_mock.assert_called_once()

    def test_advance_turn_action_saves(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("advance_turn")
            save_mock.assert_called_once()

    def test_new_day_action_saves(self):
        self.engine.state.turn = 6
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("new_day")
            save_mock.assert_called_once()

    def test_broken_block_advance_turn_saves(self):
        """broken 帐篷阻塞 advance_turn，补足 decisions_left 后仍保存"""
        self.engine.tents[1].status = "broken"
        self.engine.state.decisions_left = 0
        self.engine.state.turn = 2
        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.advance_turn()
            save_mock.assert_called_once()

class GrowthProjectActionTests(ApiPersistenceTestCase):
    def _qualify_hot_spring(self):
        self.engine.state.turn = 6
        self.engine.state.total_served_groups = 150
        self.engine.state.balance = 10000
        for tent_id in range(2, 6):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2

    def test_purchase_growth_project_success_dispatches_and_saves_once(self):
        self._qualify_hot_spring()
        with mock.patch.object(self.engine, "save_state") as save_mock:
            result = self._action(
                "purchase_growth_project",
                {"project_id": "hot_spring"},
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["project_id"], "hot_spring")
        self.assertEqual(result["price"], 8000)
        self.assertEqual(result["balance_after"], 2000)
        save_mock.assert_called_once()

    def test_purchase_growth_project_failure_does_not_save(self):
        self.engine.state.turn = 2
        with mock.patch.object(self.engine, "save_state") as save_mock:
            result = self._action(
                "purchase_growth_project",
                {"project_id": "hot_spring"},
            )

        self.assertFalse(result["success"])
        save_mock.assert_not_called()

    def test_duplicate_purchase_does_not_charge_or_save(self):
        self._qualify_hot_spring()
        with mock.patch.object(self.engine, "save_state"):
            first = self._action(
                "purchase_growth_project",
                {"project_id": "hot_spring"},
            )
        balance_after_first = self.engine.state.balance

        with mock.patch.object(self.engine, "save_state") as save_mock:
            second = self._action(
                "purchase_growth_project",
                {"project_id": "hot_spring"},
            )

        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(self.engine.state.balance, balance_after_first)
        save_mock.assert_not_called()

    def test_unknown_growth_project_does_not_save(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            result = self._action(
                "purchase_growth_project",
                {"project_id": "unknown_project"},
            )

        self.assertFalse(result["success"])
        save_mock.assert_not_called()

    def test_invalid_project_id_does_not_call_engine_or_save(self):
        for params in (None, {}, {"project_id": 12}, {"project_id": ""}):
            with self.subTest(params=params):
                with mock.patch.object(self.engine, "purchase_growth_project") as purchase_mock:
                    with mock.patch.object(self.engine, "save_state") as save_mock:
                        with self.assertRaises(game_api.HTTPException):
                            self._action("purchase_growth_project", params)
                purchase_mock.assert_not_called()
                save_mock.assert_not_called()


class GrowthQueryTests(ApiPersistenceTestCase):
    def _qualify_hot_spring(self):
        self.engine.state.turn = 6
        self.engine.state.total_served_groups = 150
        self.engine.state.balance = 10000
        for tent_id in range(2, 6):
            self.engine.tents[tent_id].is_unlocked = True
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2

    def _hot_spring_project(self, response):
        return next(
            project for project in response["projects"]
            if project["project_id"] == "hot_spring"
        )

    def test_growth_query_calls_both_engine_readers_and_returns_hot_spring(self):
        with mock.patch.object(
            self.engine,
            "get_growth_progress",
            wraps=self.engine.get_growth_progress,
        ) as progress_mock, mock.patch.object(
            self.engine,
            "get_growth_project_catalog",
            wraps=self.engine.get_growth_project_catalog,
        ) as catalog_mock, mock.patch.object(self.engine, "save_state") as save_mock:
            response = game_api.get_growth()

        self.assertTrue(response["success"])
        self.assertIn("progress", response)
        self.assertIn("projects", response)
        self.assertEqual(response["progress"]["hot_spring_built"], False)
        self.assertEqual(self._hot_spring_project(response)["project_id"], "hot_spring")
        progress_mock.assert_any_call()
        self.assertGreaterEqual(progress_mock.call_count, 1)
        catalog_mock.assert_called_once_with()
        save_mock.assert_not_called()

    def test_growth_query_preserves_unmet_and_qualified_states_without_mutation(self):
        balance_before = self.engine.state.balance
        built_before = self.engine.state.hot_spring_built
        unmet_response = game_api.get_growth()
        unmet_project = self._hot_spring_project(unmet_response)
        self.assertFalse(unmet_project["can_purchase_now"])
        self.assertTrue(unmet_project["unmet_conditions"])

        self._qualify_hot_spring()
        qualified_response = game_api.get_growth()
        qualified_project = self._hot_spring_project(qualified_response)
        self.assertTrue(qualified_project["can_purchase_now"])
        self.assertEqual(self.engine.state.balance, 10000)
        self.assertFalse(self.engine.state.hot_spring_built)
        self.assertEqual(balance_before, 1000)
        self.assertFalse(built_before)

        self.engine.purchase_growth_project("hot_spring")
        purchased_response = game_api.get_growth()
        purchased_project = self._hot_spring_project(purchased_response)
        self.assertTrue(purchased_project["completed"])
        self.assertFalse(purchased_project["can_purchase_now"])
        self.assertEqual(purchased_response["progress"]["hot_spring_built"], True)


class DatabaseRecoveryTests(ApiPersistenceTestCase):
    """验证真实数据库恢复后的副作用一致性"""

    def test_turn_plan_submission_recovery_preserves_pending_plan(self):
        game_api.engine = self.engine
        self.assertIs(game_api.get_engine(), self.engine)

        self.engine.state.turn = 2
        self.engine.state.decisions_left = 3

        result = self._plan()
        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.turn, 2)
        self.assertEqual(self.engine.state.decisions_left, 0)
        self.assertIsNotNone(self.engine.state.pending_turn_plan)
        self.assertEqual(
            self.engine.state.pending_turn_plan["target_day"],
            1,
        )
        self.assertEqual(
            self.engine.state.pending_turn_plan["target_turn"],
            2,
        )

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE id = 1"
            ).fetchone()
        self.assertIsNotNone(row)
        snapshot = json.loads(row[0])
        state = snapshot["state"]
        self.assertEqual(state["day"], 1)
        self.assertEqual(state["turn"], 2)
        self.assertEqual(state["decisions_left"], 0)
        self.assertIsNotNone(state["pending_turn_plan"])

        restored = self._new_engine_from_db()
        self.assertEqual(restored.state.turn, 2)
        self.assertEqual(restored.state.decisions_left, 0)
        self.assertIsNotNone(restored.state.pending_turn_plan)
        self.assertEqual(
            restored.state.pending_turn_plan["target_day"],
            1,
        )
        self.assertEqual(
            restored.state.pending_turn_plan["target_turn"],
            2,
        )
        self.assertEqual(
            restored.state.pending_turn_plan["actions"],
            [],
        )

    def test_clean_tents_recovery(self):
        """清洁帐篷后恢复，状态保持为 available"""
        self.engine.state.turn = 6
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[3].status = "cleaning"

        self._action("clean_tents", {"tent_ids": [1, 3]})

        restored = self._new_engine_from_db()
        self.assertEqual(restored.tents[1].status, "available")
        self.assertEqual(restored.tents[3].status, "available")

    def test_upgrade_facility_recovery(self):
        """升级设施后恢复，等级和余额变化仍存在"""
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_dining_groups = 8
        initial_level = self.engine.facilities["dining"].level
        initial_balance = self.engine.state.balance

        self._action("upgrade_facility", {"facility_name": "dining"})

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["dining"].level, initial_level + 1)
        self.assertLess(restored.state.balance, initial_balance)

    def test_entertainment_upgrade_recovery(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_paid_entertainment_groups = 8
        initial_level = self.engine.facilities["entertainment"].level
        initial_balance = self.engine.state.balance

        self._action("upgrade_facility", {"facility_name": "entertainment"})

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["entertainment"].level, initial_level + 1)
        self.assertLess(restored.state.balance, initial_balance)

    def test_dining_food_stock_recovery(self):
        guest = NPCGroup(
            id=self.engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
            total_satisfaction=60,
        )
        self.engine.npc_pool.append(guest)
        self.engine.state.food_stock = 2
        self.engine.facilities["dining"].level = 1

        with mock.patch("game_engine.random.random", return_value=0.0):
            self.engine._process_dining({"events": []})
        self.assertTrue(self.engine.save_state())

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["dining"].level, 1)
        self.assertEqual(restored.state.food_stock, 0)
        self.assertEqual(restored.state.today_income["dining"], 90)
        self.assertEqual(restored.npc_pool[0].last_dining_day, restored.state.day)


    def test_turn6_food_preorder_recovery_blocks_repeat_same_day(self):
        self.engine.state.turn = 6
        opening_stock = self.engine.state.food_stock

        result = self._action("buy_food_package", {"package_key": "medium"})
        self.assertTrue(result["success"])

        restored = self._new_engine_from_db()
        self.assertEqual(
            restored.state.food_stock,
            opening_stock + CampingPlazaEngine.FOOD_PACKAGES["medium"]["portions"],
        )
        self.assertEqual(restored.state.last_food_preorder_day, restored.state.day)

        repeat = restored.buy_food_package("small")
        self.assertFalse(repeat["success"])


class IsolationTests(ApiPersistenceTestCase):
    """测试隔离性"""

    def test_no_formal_database_touched(self):
        formal_db = os.path.join(_PROJECT_ROOT, "camping_plaza", "camping_plaza.db")
        self.assertFalse(
            os.path.exists(formal_db),
            "测试不得创建或修改正式 camping_plaza.db"
        )


class HiddenPendingReviewApiTests(ApiPersistenceTestCase):
    """待结算评价不应通过 API 提前暴露"""

    def test_api_state_hides_pending_reviews(self):
        self.engine.state.pending_reviews = [{
            "created_day": 1,
            "rating": 5,
            "npc_id": 11,
            "visit_type": "day",
            "group_size": 2,
        }]

        state = game_api.get_state()

        self.assertNotIn("pending_reviews", state)

    def test_mcp_state_hides_pending_reviews(self):
        self.engine.state.pending_reviews = [{
            "created_day": 1,
            "rating": 5,
            "npc_id": 11,
            "visit_type": "day",
            "group_size": 2,
        }]

        state = game_api.mcp_state()

        self.assertNotIn("pending_reviews", state)


class TurnPlanApiTests(ApiPersistenceTestCase):
    def test_empty_plan_can_submit_and_cannot_resubmit(self):
        self.engine.state.turn = 2

        first = self._plan()
        second = self._plan()

        self.assertTrue(first["success"])
        self.assertEqual(first["action_count"], 0)
        self.assertFalse(second["success"])

    def test_one_and_three_actions_can_submit(self):
        self.engine.state.turn = 2
        result = self._plan(
            actions=[game_api.ActionRequest(action="improve_service")]
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["action_count"], 1)

        self.engine.state.pending_turn_plan = None
        self.engine.state.decisions_left = 3
        result = self._plan(
            actions=[
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
            ]
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["action_count"], 3)

    def test_four_actions_rejected_and_free_actions_do_not_count(self):
        self.engine.state.turn = 2
        result = self._plan(
            actions=[
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
            ]
        )
        self.assertFalse(result["success"])

        result = self._plan(
            free_actions=[
                game_api.ActionRequest(
                    action="clean_tents", params={"tent_ids": [1, 2]}
                )
            ],
            actions=[
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="improve_service"),
            ]
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["free_action_count"], 1)
        self.assertEqual(result["action_count"], 3)

    def test_turn1_and_turn6_submission_rejected(self):
        for turn in (1, 6):
            self.engine.state.turn = turn
            self.engine.state.pending_turn_plan = None
            self.engine.state.decisions_left = 3
            result = self._plan()
            self.assertFalse(result["success"])

    def test_submit_does_not_apply_effect_immediately(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "broken"
        balance_before = self.engine.state.balance

        result = self._plan(
            actions=[game_api.ActionRequest(action="repair_tent", params={"tent_id": 1})]
        )

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.tents[1].status, "broken")
        self.assertEqual(self.engine.state.balance, balance_before)

    def test_business_turn_immediate_actions_are_rejected(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[2].status = "broken"

        clean_result = self._action("clean_tents", {"tent_ids": [1]})
        repair_result = self._action("repair_tent", {"tent_id": 2})
        improve_result = self._action("improve_service")

        self.assertFalse(clean_result["success"])
        self.assertFalse(repair_result["success"])
        self.assertFalse(improve_result["success"])
        self.assertEqual(
            clean_result["message"],
            "请通过 /api/turn/plan 安排下一营业Turn行动。"
        )

    def test_business_turn_direct_food_purchase_is_rejected(self):
        self.engine.state.turn = 2

        result = self._action("buy_food_package", {"package_key": "small"})

        self.assertFalse(result["success"])

    def test_turn6_immediate_management_actions_still_work(self):
        self.engine.state.turn = 6
        self.engine.tents[1].status = "broken"
        self.engine.state.decisions_left = 3

        result = self._action("repair_tent", {"tent_id": 1})

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.tents[1].status, "available")

    def test_turn6_buy_food_package_works_once(self):
        self.engine.state.turn = 6
        opening_stock = self.engine.state.food_stock

        first = self._action("buy_food_package", {"package_key": "small"})
        second = self._action("buy_food_package", {"package_key": "medium"})

        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(
            self.engine.state.food_stock,
            opening_stock + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"],
        )

class McpTurnPlanTests(ApiPersistenceTestCase):
    def test_mcp_state_exposes_food_stock(self):
        self.engine.state.food_stock = 9

        state = game_api.mcp_state()

        self.assertEqual(state["food_stock"], 9)

    def test_mcp_state_exposes_greenery_summary(self):
        self.engine.facilities["greenery"].level = 1
        self.engine.facilities["greenery"].greenery_satisfaction = 6.5
        self.engine.state.greenery_processed_today = True
        day_before = self.engine.state.day
        turn_before = self.engine.state.turn

        expected_greenery = self.engine.get_full_state()["greenery"]

        with mock.patch.object(self.engine, "save_state") as save_mock:
            state = game_api.mcp_state()
            save_mock.assert_not_called()

        self.assertEqual(state["greenery"], expected_greenery)
        self.assertEqual(
            set(state["greenery"].keys()),
            {"level", "value", "max", "maintained_today", "decay_next_day"},
        )
        self.assertNotIn("turn_settled", state)
        self.assertNotIn("phase", state)
        self.assertNotIn("available_actions", state)

        for field in [
            "hot_spring",
            "day_campsite",
            "arrival_plan",
            "reservation",
            "planning_available",
            "plan_submitted",
            "plan_target_turn",
            "next_turn_checkout_tents",
        ]:
            self.assertIn(field, state)

        self.assertEqual(self.engine.facilities["greenery"].greenery_satisfaction, 6.5)
        self.assertTrue(self.engine.state.greenery_processed_today)
        self.assertEqual(self.engine.state.day, day_before)
        self.assertEqual(self.engine.state.turn, turn_before)

    def test_mcp_state_exposes_turn_plan_flags_only(self):
        self.engine.state.turn = 2

        state = game_api.mcp_state()
        self.assertTrue(state["planning_available"])
        self.assertFalse(state["plan_submitted"])
        self.assertIsNone(state["plan_target_turn"])
        self.assertNotIn("pending_turn_plan", state)
        self.assertNotIn("free_actions", state)
        self.assertNotIn("actions", state)

        self._plan()
        state = game_api.mcp_state()
        self.assertFalse(state["planning_available"])
        self.assertTrue(state["plan_submitted"])
        self.assertEqual(state["plan_target_turn"], 2)
        self.assertEqual(state["next_turn_checkout_tents"], [])

    def test_mcp_state_exposes_next_turn_checkout_tents(self):
        self.engine.state.turn = 2
        guest = NPCGroup(
            id=self.engine._next_npc_id(),
            group_size=1,
            visit_type="overnight",
            location="tent_1",
            checkout_turn=2,
        )
        self.engine.npc_pool.append(guest)
        self.engine.tents[1].status = "occupied"
        self.engine.tents[1].occupied_by = guest.id

        state = game_api.mcp_state()
        self.assertEqual(state["next_turn_checkout_tents"], [1])
        self.assertNotIn("checkout_turn", json.dumps(state, ensure_ascii=False))

    def test_dining_upgrade_reaches_lv2_and_then_stops_without_charge(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_dining_groups = 36

        first = self._action("upgrade_facility", {"facility_name": "dining"})
        second = self._action("upgrade_facility", {"facility_name": "dining"})
        balance_before_third = self.engine.state.balance
        third = self._action("upgrade_facility", {"facility_name": "dining"})

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertFalse(third["success"])
        self.assertEqual(self.engine.facilities["dining"].level, 2)
        self.assertEqual(self.engine.state.balance, 99999 - 700 - 1800)
        self.assertEqual(self.engine.state.balance, balance_before_third)

    def test_entertainment_upgrade_reaches_lv2_and_then_stops_without_charge(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_paid_entertainment_groups = 32

        first = self._action("upgrade_facility", {"facility_name": "entertainment"})
        second = self._action("upgrade_facility", {"facility_name": "entertainment"})
        balance_before_third = self.engine.state.balance
        third = self._action("upgrade_facility", {"facility_name": "entertainment"})

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertFalse(third["success"])
        self.assertEqual(self.engine.facilities["entertainment"].level, 2)
        self.assertEqual(self.engine.state.balance, 99999 - 600 - 1600)
        self.assertEqual(self.engine.state.balance, balance_before_third)

    def test_greenery_lv2_upgrade_still_fails_without_charge(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.facilities["greenery"].level = 2
        balance_before = self.engine.state.balance

        result = self._action("upgrade_facility", {"facility_name": "greenery"})

        self.assertFalse(result["success"])
        self.assertEqual(self.engine.facilities["greenery"].level, 2)
        self.assertEqual(self.engine.state.balance, balance_before)

    def test_mcp_actions_switch_between_plan_and_turn6_management(self):
        self.engine.state.turn = 2
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]
        self.assertIn("submit_turn_plan", action_names)
        self.assertNotIn("clean_tents", action_names)
        self.assertNotIn("repair_tent", action_names)
        self.assertNotIn("improve_service", action_names)

        self._plan()
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]
        self.assertNotIn("submit_turn_plan", action_names)
        self.assertIn("advance_turn", action_names)

        self.engine.state.turn = 6
        self.engine.state.pending_turn_plan = None
        self.engine.tents[1].status = "broken"
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[2].status = "cleaning"
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]
        self.assertNotIn("submit_turn_plan", action_names)
        self.assertIn("repair_tent", action_names)
        self.assertIn("clean_tents", action_names)

    def test_mcp_actions_hide_facility_upgrades_at_lv2(self):
        self.engine.state.turn = 6
        self.engine.facilities["dining"].level = 2
        self.engine.facilities["entertainment"].level = 2
        self.engine.facilities["greenery"].level = 2

        actions = game_api.mcp_available_actions()["available_actions"]
        upgrade_facility_actions = [
            item for item in actions if item["action"] == "upgrade_facility"
        ]

        self.assertEqual(upgrade_facility_actions, [])

    def test_mcp_actions_expose_turn6_food_preorder_packages(self):
        self.engine.state.turn = 6

        actions = game_api.mcp_available_actions()["available_actions"]
        food_actions = [item for item in actions if item["action"] == "buy_food_package"]

        self.assertEqual(len(food_actions), 3)
        self.assertEqual(
            sorted(item["params"]["package_key"] for item in food_actions),
            sorted(CampingPlazaEngine.FOOD_PACKAGES.keys()),
        )
        for item in food_actions:
            package = CampingPlazaEngine.FOOD_PACKAGES[item["params"]["package_key"]]
            self.assertIn(package["name"], item["description"])
            self.assertIn(str(package["portions"]), item["description"])
            self.assertIn(str(package["price"]), item["description"])

    def test_mcp_actions_hide_turn6_food_preorder_after_success(self):
        self.engine.state.turn = 6
        self._action("buy_food_package", {"package_key": "small"})

        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]

        self.assertNotIn("buy_food_package", action_names)

    def test_mcp_plan_description_mentions_food_purchase_action(self):
        self.engine.state.turn = 2

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(item for item in actions if item["action"] == "submit_turn_plan")

        self.assertIn("buy_food_package", submit_action["description"])
        self.assertIn("package_key", submit_action["description"])


class HotSpringStateOutputTests(ApiPersistenceTestCase):
    """三个只读状态输出应统一携带温泉当前营业状态"""

    def test_three_outputs_share_same_hot_spring_status(self):
        self.engine.state.hot_spring_built = True
        self.engine.state.hot_spring_people_served_today = 7
        self.engine.state.today_income["hot_spring"] = 560

        expected = {
            "built": True,
            "people_served_today": 7,
            "remaining_capacity_today": (
                CampingPlazaEngine.HOT_SPRING_DAILY_CAPACITY - 7
            ),
            "today_income": 560,
        }

        self.assertEqual(game_api.get_state()["hot_spring"], expected)
        self.assertEqual(game_api.get_display_state()["data"]["hot_spring"], expected)
        self.assertEqual(game_api.mcp_state()["hot_spring"], expected)

    def test_remaining_capacity_uses_authoritative_capacity(self):
        self.engine.state.hot_spring_built = True
        self.engine.state.hot_spring_people_served_today = 7

        state = game_api.get_state()["hot_spring"]

        self.assertEqual(
            state["remaining_capacity_today"],
            CampingPlazaEngine.HOT_SPRING_DAILY_CAPACITY - 7,
        )

    def test_default_unbuilt_status(self):
        state = game_api.get_state()["hot_spring"]

        self.assertFalse(state["built"])
        self.assertEqual(state["people_served_today"], 0)
        self.assertEqual(
            state["remaining_capacity_today"],
            CampingPlazaEngine.HOT_SPRING_DAILY_CAPACITY,
        )
        self.assertEqual(state["today_income"], 0)

    def test_read_only_outputs_do_not_save_or_mutate(self):
        self.engine.state.hot_spring_built = True
        self.engine.state.hot_spring_people_served_today = 7
        self.engine.state.today_income["hot_spring"] = 560

        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.get_state()
            game_api.get_display_state()
            game_api.mcp_state()
            save_mock.assert_not_called()

        self.assertEqual(self.engine.state.hot_spring_built, True)
        self.assertEqual(self.engine.state.hot_spring_people_served_today, 7)
        self.assertEqual(self.engine.state.today_income["hot_spring"], 560)


class DayCampsiteStateOutputTests(ApiPersistenceTestCase):
    """三个只读状态输出应统一携带日间营位当天容量状态"""

    def _add_pending_day_reservation(self, npc_id):
        """在 today_arrival_plan 中加入一条符合现行规则的待到达日间预约。"""
        self.engine.state.today_arrival_plan.append({
            "npc_id": npc_id,
            "planned_day": self.engine.state.day,
            "source": "reservation",
            "visit_type": "day",
            "arrival_status": "pending",
            "paid": True,
        })

    def test_default_state(self):
        state = game_api.get_state()["day_campsite"]

        self.assertEqual(
            state["group_capacity_per_day"],
            CampingPlazaEngine.DAY_CAMPSITE_CAPACITY,
        )
        self.assertEqual(state["groups_served_today"], 0)
        self.assertEqual(
            state["remaining_groups_today"],
            CampingPlazaEngine.DAY_CAMPSITE_CAPACITY,
        )

    def test_served_and_reservation_occupancy_state(self):
        self.engine.state.day_campsite_groups_served = 6
        self._add_pending_day_reservation(npc_id=9001)

        expected_remaining = (
            CampingPlazaEngine.DAY_CAMPSITE_CAPACITY - 6 - 1
        )
        self.assertEqual(
            self.engine.get_day_campsite_remaining(),
            expected_remaining,
        )

        expected = {
            "group_capacity_per_day": CampingPlazaEngine.DAY_CAMPSITE_CAPACITY,
            "groups_served_today": 6,
            "remaining_groups_today": expected_remaining,
        }

        self.assertEqual(game_api.get_state()["day_campsite"], expected)
        self.assertEqual(game_api.get_display_state()["data"]["day_campsite"], expected)
        self.assertEqual(game_api.mcp_state()["day_campsite"], expected)

    def test_read_only_outputs_do_not_save_or_mutate_and_keep_hot_spring(self):
        self.engine.state.day_campsite_groups_served = 6
        self._add_pending_day_reservation(npc_id=9002)
        plan_before = list(self.engine.state.today_arrival_plan)
        hot_spring_before = game_api.get_state()["hot_spring"]

        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.get_state()
            game_api.get_display_state()
            game_api.mcp_state()
            save_mock.assert_not_called()

        self.assertEqual(self.engine.state.day_campsite_groups_served, 6)
        self.assertEqual(self.engine.state.today_arrival_plan, plan_before)
        self.assertEqual(game_api.get_state()["hot_spring"], hot_spring_before)


class ArrivalPlanSummaryTests(ApiPersistenceTestCase):
    """三个只读状态输出应统一携带今日到达计划的安全摘要"""

    def test_empty_plan(self):
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = []

        summary = game_api.get_state()["arrival_plan"]

        self.assertEqual(summary["total_groups"], 0)
        self.assertEqual(summary["total_people"], 0)
        self.assertEqual(summary["pending_groups"], 0)
        self.assertEqual(summary["pending_people"], 0)
        self.assertEqual(summary["arrived_groups"], 0)
        self.assertEqual(summary["turned_away_full_groups"], 0)

        self.assertEqual(
            set(summary["pending_by_turn"].keys()),
            {"2", "3", "4"},
        )
        for turn_bucket in summary["pending_by_turn"].values():
            self.assertEqual(turn_bucket["day_groups"], 0)
            self.assertEqual(turn_bucket["day_people"], 0)
            self.assertEqual(turn_bucket["overnight_groups"], 0)
            self.assertEqual(turn_bucket["overnight_people"], 0)

    def test_mixed_plan_aggregation(self):
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            {
                "npc_id": 5001,
                "group_size": 2,
                "visit_type": "day",
                "arrival_turn": 2,
                "planned_day": self.engine.state.day,
                "arrival_status": "pending",
                "source": "natural_day",
                "economic_level": 2,
                "spending_habit": 1,
                "temperament": 0,
                "total_satisfaction": 70,
                "paid": True,
                "is_reserved": False,
                "tent_id": None,
                "day_to_overnight_intent": False,
                "planned_actions": [{"action": "dine", "menu_key": "hotpot"}],
            },
            {
                "npc_id": 5002,
                "group_size": 4,
                "visit_type": "overnight",
                "arrival_turn": 3,
                "planned_day": self.engine.state.day,
                "arrival_status": "pending",
                "source": "natural_overnight",
                "economic_level": 1,
                "spending_habit": 2,
                "temperament": 1,
                "total_satisfaction": 60,
                "paid": True,
                "is_reserved": False,
                "tent_id": 3,
                "day_to_overnight_intent": False,
                "planned_actions": [{"action": "dine", "menu_key": "bbq"}],
            },
            {
                "npc_id": 5003,
                "group_size": 3,
                "visit_type": "day",
                "arrival_turn": 2,
                "planned_day": self.engine.state.day,
                "arrival_status": "arrived",
                "source": "natural_day",
            },
            {
                "npc_id": 5004,
                "group_size": 2,
                "visit_type": "overnight",
                "arrival_turn": 4,
                "planned_day": self.engine.state.day,
                "arrival_status": "turned_away_full",
                "source": "natural_overnight",
            },
            {
                # 非当前日计划，必须被忽略
                "npc_id": 5005,
                "group_size": 9,
                "visit_type": "day",
                "arrival_turn": 2,
                "planned_day": self.engine.state.day + 1,
                "arrival_status": "pending",
                "source": "natural_day",
            },
        ]

        summary = game_api.get_state()["arrival_plan"]

        self.assertEqual(summary["day"], self.engine.state.day)
        self.assertEqual(summary["total_groups"], 4)
        self.assertEqual(summary["total_people"], 2 + 4 + 3 + 2)
        self.assertEqual(summary["pending_groups"], 2)
        self.assertEqual(summary["pending_people"], 2 + 4)
        self.assertEqual(summary["arrived_groups"], 1)
        self.assertEqual(summary["turned_away_full_groups"], 1)

        pending_by_turn = summary["pending_by_turn"]
        self.assertEqual(pending_by_turn["2"]["day_groups"], 1)
        self.assertEqual(pending_by_turn["2"]["day_people"], 2)
        self.assertEqual(pending_by_turn["2"]["overnight_groups"], 0)
        self.assertEqual(pending_by_turn["2"]["overnight_people"], 0)
        self.assertEqual(pending_by_turn["3"]["overnight_groups"], 1)
        self.assertEqual(pending_by_turn["3"]["overnight_people"], 4)
        self.assertEqual(pending_by_turn["3"]["day_groups"], 0)
        self.assertEqual(pending_by_turn["3"]["day_people"], 0)
        self.assertEqual(pending_by_turn["4"]["overnight_groups"], 0)
        self.assertEqual(pending_by_turn["4"]["overnight_people"], 0)
        self.assertEqual(pending_by_turn["4"]["day_groups"], 0)
        self.assertEqual(pending_by_turn["4"]["day_people"], 0)

    def test_three_outputs_share_same_arrival_plan(self):
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            {
                "npc_id": 6001,
                "group_size": 2,
                "visit_type": "day",
                "arrival_turn": 2,
                "planned_day": self.engine.state.day,
                "arrival_status": "pending",
                "source": "natural_day",
            }
        ]

        expected = game_api.get_state()["arrival_plan"]
        self.assertEqual(
            game_api.get_display_state()["data"]["arrival_plan"],
            expected,
        )
        self.assertEqual(game_api.mcp_state()["arrival_plan"], expected)

    def test_summary_exposes_no_sensitive_fields(self):
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            {
                "npc_id": 7001,
                "group_size": 2,
                "visit_type": "day",
                "arrival_turn": 2,
                "planned_day": self.engine.state.day,
                "arrival_status": "pending",
                "source": "reservation",
                "paid": True,
                "is_reserved": True,
                "tent_id": 5,
                "economic_level": 3,
                "spending_habit": 1,
                "temperament": 0,
                "total_satisfaction": 80,
                "day_to_overnight_intent": True,
                "planned_actions": [{"action": "dine", "menu_key": "steak"}],
            }
        ]

        dumped = json.dumps(game_api.get_state()["arrival_plan"], ensure_ascii=False)

        for forbidden in [
            "npc_id",
            "economic_level",
            "spending_habit",
            "temperament",
            "total_satisfaction",
            "source",
            "paid",
            "tent_id",
            "planned_actions",
            "menu_key",
            "day_to_overnight_intent",
        ]:
            self.assertNotIn(forbidden, dumped)

    def test_read_only_arrival_plan_outputs_do_not_save_or_mutate(self):
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        plan_before = [
            {
                "npc_id": 8001,
                "group_size": 2,
                "visit_type": "day",
                "arrival_turn": 2,
                "planned_day": self.engine.state.day,
                "arrival_status": "pending",
                "source": "natural_day",
            }
        ]
        self.engine.state.today_arrival_plan = list(plan_before)

        with mock.patch.object(self.engine, "save_state") as save_mock:
            with mock.patch.object(self.engine, "_ensure_today_arrival_plan") as ensure_mock:
                game_api.get_state()
                game_api.get_display_state()
                game_api.mcp_state()
                save_mock.assert_not_called()
                ensure_mock.assert_not_called()

        self.assertEqual(self.engine.state.today_arrival_plan, plan_before)
        self.assertEqual(self.engine.state.today_arrival_plan_day, self.engine.state.day)

        state = game_api.get_state()
        self.assertIn("hot_spring", state)
        self.assertIn("day_campsite", state)


class McpGrowthActionTests(ApiPersistenceTestCase):
    """Turn 6 日终管理应按现有成长目录动态加入可购买成长项目动作"""

    def _growth_purchase_actions(self, actions):
        return [
            a for a in actions
            if a["action"] == "purchase_growth_project"
        ]

    def test_purchasable_project_appears_in_turn6(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7  # 满足 tent_2 的 fallback_operating_day
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        tent2 = next(
            a for a in purchases
            if a["params"]["project_id"] == "tent_2"
        )
        self.assertEqual(tent2["params"], {"project_id": "tent_2"})
        self.assertIn("2号帐篷", tent2["description"])
        self.assertIn("600金币", tent2["description"])

        purchase_ids = [a["params"]["project_id"] for a in purchases]
        self.assertEqual(len(purchase_ids), len(set(purchase_ids)))

    def test_insufficient_balance_project_not_offered(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7  # 满足 tent_2 经营条件
        self.engine.state.balance = 100  # 低于 tent_2 价格 600

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        self.assertTrue(all(
            a["params"]["project_id"] != "tent_2"
            for a in purchases
        ))

    def test_completed_project_not_offered(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7
        self.engine.state.balance = 10000
        self.engine.tents[2].is_unlocked = True  # tent_2 已完成

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        self.assertTrue(all(
            a["params"]["project_id"] != "tent_2"
            for a in purchases
        ))

    def test_prerequisite_unmet_project_not_offered(self):
        # tent_3 前置 tent_2 未解锁，验证前置未满足时不出现
        self.engine.state.turn = 6
        self.engine.state.day = 12  # tent_3 经营 fallback 满足，但前置 tent_2 未解锁
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        self.assertTrue(all(
            a["params"]["project_id"] != "tent_3"
            for a in purchases
        ))

    def test_operation_requirement_unmet_project_not_offered(self):
        # hot_spring 需要 required_growth_nodes=8，默认未满足经营条件
        self.engine.state.turn = 6
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        self.assertTrue(all(
            a["params"]["project_id"] != "hot_spring"
            for a in purchases
        ))

    def test_non_turn6_does_not_offer_growth_purchase(self):
        self.engine.state.turn = 2
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        self.assertEqual(purchases, [])
        self.assertTrue(any(a["action"] == "submit_turn_plan" for a in actions))

    def test_turn6_keeps_existing_actions(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [a["action"] for a in actions]

        self.assertIn("manage_greenery", action_names)
        self.assertIn("new_day", action_names)
        self.assertIn("purchase_growth_project", action_names)
        self.assertNotIn("upgrade_facility", action_names)

    def test_turn6_does_not_expose_legacy_upgrade_facility(self):
        # 即使设施等级低于最高等级，也不能再出现旧的 upgrade_facility 动作
        self.engine.state.turn = 6
        self.engine.state.day = 7
        self.engine.state.balance = 10000
        self.engine.facilities["dining"].level = 0
        self.engine.facilities["entertainment"].level = 0
        self.engine.facilities["greenery"].level = 0

        actions = game_api.mcp_available_actions()["available_actions"]

        self.assertTrue(all(
            a["action"] != "upgrade_facility"
            for a in actions
        ))

    def test_facility_growth_project_exposed_via_purchase_action(self):
        # 构造一个真实设施成长项目（dining_lv1）满足购买条件，确认通过
        # purchase_growth_project 暴露，且不同时出现语义重复的 upgrade_facility
        self.engine.state.turn = 6
        self.engine.state.balance = 10000
        self.engine.state.successful_dining_groups = 8  # dining_lv1 经营条件

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)

        dining1 = next(
            a for a in purchases
            if a["params"]["project_id"] == "dining_lv1"
        )
        self.assertEqual(dining1["params"], {"project_id": "dining_lv1"})
        self.assertIn("餐饮", dining1["description"])
        self.assertIn("700金币", dining1["description"])

        self.assertTrue(all(
            a["action"] != "upgrade_facility"
            for a in actions
        ))

    def test_generated_action_matches_execution_entry(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        purchases = self._growth_purchase_actions(actions)
        self.assertTrue(purchases)

        entry = purchases[0]
        request = game_api.ActionRequest(action=entry["action"], params=entry["params"])
        self.assertEqual(request.action, "purchase_growth_project")
        self.assertIn("project_id", request.params)

    def test_read_only_does_not_mutate_or_save(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7
        self.engine.state.balance = 10000
        balance_before = self.engine.state.balance
        progress_before = self.engine.get_growth_progress()
        tent2_unlocked_before = self.engine.tents[2].is_unlocked
        dining_level_before = self.engine.facilities["dining"].level

        with mock.patch.object(self.engine, "save_state") as save_mock:
            with mock.patch.object(
                self.engine, "purchase_growth_project"
            ) as purchase_mock, mock.patch.object(
                self.engine, "upgrade_facility"
            ) as upgrade_mock:
                game_api.mcp_available_actions()
                save_mock.assert_not_called()
                purchase_mock.assert_not_called()
                upgrade_mock.assert_not_called()

        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.get_growth_progress(), progress_before)
        self.assertEqual(self.engine.tents[2].is_unlocked, tent2_unlocked_before)
        self.assertEqual(self.engine.facilities["dining"].level, dining_level_before)


if __name__ == "__main__":
    unittest.main()
