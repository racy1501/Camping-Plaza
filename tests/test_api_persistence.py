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

from fastapi.testclient import TestClient
from pydantic import ValidationError

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
        # Turn 6 日终批处理路径
        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.submit_day_end(
                game_api.DayEndRequest(day_end_actions=[
                    game_api.ActionRequest(action="manage_greenery",
                                           params={"action": "maintain"}),
                ])
            )
            save_mock.assert_called_once()

    def test_buy_food_package_saves(self):
        self.engine.state.turn = 6
        # Turn 6 日终批处理路径
        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.submit_day_end(
                game_api.DayEndRequest(day_end_actions=[
                    game_api.ActionRequest(action="buy_food_package",
                                           params={"package_key": "small"}),
                ])
            )
            save_mock.assert_called_once()

    def test_advance_turn_action_saves(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("advance_turn")
            save_mock.assert_called_once()

    def test_new_day_action_saves(self):
        self.engine.state.turn = 6
        # Turn 6 日终批处理：先提交空清单，再开启下一天
        game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[])
        )
        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.start_next_day()
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
        # Turn 6 日终批处理路径
        with mock.patch.object(self.engine, "save_state") as save_mock:
            result = game_api.submit_day_end(
                game_api.DayEndRequest(day_end_actions=[
                    game_api.ActionRequest(action="purchase_growth_project",
                                           params={"project_id": "hot_spring"}),
                ])
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["day_end_completed"])
        self.assertEqual(result["turn"], 6)
        self.assertEqual(len(result["results"]), 1)
        item = result["results"][0]
        self.assertTrue(item["success"])
        self.assertEqual(item["project_id"], "hot_spring")
        self.assertEqual(item["price"], 3000)
        self.assertEqual(item["balance_after"], 7000)
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
        """同一份 /api/day/end 清单中重复购买同一成长项目：首次成功，第二次真实业务失败，金币只扣一次"""
        self._qualify_hot_spring()
        balance_before = self.engine.state.balance
        # 同一份清单提交两次相同的真实 purchase_growth_project
        with mock.patch.object(self.engine, "save_state") as save_mock:
            result = game_api.submit_day_end(
                game_api.DayEndRequest(day_end_actions=[
                    game_api.ActionRequest(action="purchase_growth_project",
                                           params={"project_id": "hot_spring"}),
                    game_api.ActionRequest(action="purchase_growth_project",
                                           params={"project_id": "hot_spring"}),
                ])
            )

        self.assertTrue(result["success"])
        self.assertEqual(len(result["results"]), 2)
        # 第一次成功
        self.assertTrue(result["results"][0]["success"])
        self.assertEqual(result["results"][0]["project_id"], "hot_spring")
        # 第二次真实业务失败（已购买，不可再购）
        self.assertFalse(result["results"][1]["success"])
        self.assertEqual(
            result["results"][1]["error_code"],
            "growth_project_not_purchasable",
        )
        # 日终动作只扣一次；跨日预约收入不影响该动作的结算结果。
        self.assertEqual(
            result["results"][0]["balance_after"],
            balance_before - 3000,
        )
        # save_state 只调用一次（整个 /api/day/end 一次保存）
        save_mock.assert_called_once()

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

    def test_turn_plan_submission_recovery_preserves_executed_state(self):
        game_api.engine = self.engine
        self.assertIs(game_api.get_engine(), self.engine)

        self.engine.state.turn = 2
        self.engine.state.decisions_left = 3
        balance_before = self.engine.state.balance
        food_stock_before = self.engine.state.food_stock

        result = self._plan(
            actions=[
                game_api.ActionRequest(
                    action="buy_food_package", params={"package_key": "small"}
                )
            ]
        )
        self.assertTrue(result["success"])
        self.assertIn("events", result)
        self.assertNotIn("plan_execution", result)
        self.assertEqual(self.engine.state.turn, 3)
        balance_after = self.engine.state.balance
        food_stock_after = self.engine.state.food_stock
        self.assertNotEqual(balance_after, balance_before)
        self.assertGreater(food_stock_after, food_stock_before)
        self.assertIsNone(self.engine.state.pending_turn_plan)

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = ?",
                (self.engine.session_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        snapshot = json.loads(row[0])
        state = snapshot["state"]
        self.assertEqual(state["day"], 1)
        self.assertEqual(state["turn"], 3)
        self.assertEqual(state["balance"], balance_after)
        self.assertEqual(state["food_stock"], food_stock_after)
        self.assertIsNone(state["pending_turn_plan"])

        restored = self._new_engine_from_db()
        self.assertEqual(restored.state.turn, 3)
        self.assertEqual(restored.state.balance, balance_after)
        self.assertEqual(restored.state.food_stock, food_stock_after)
        self.assertIsNone(restored.state.pending_turn_plan)
        self.assertEqual(restored.state.turn, self.engine.state.turn)
        self.assertEqual(restored.state.balance, self.engine.state.balance)
        self.assertEqual(restored.state.food_stock, self.engine.state.food_stock)

    def test_clean_tents_recovery(self):
        """清洁帐篷后恢复，状态保持为 available"""
        self.engine.state.turn = 6
        self.engine.tents[1].status = "cleaning"

        # Turn 6 日终批处理路径
        game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="clean_tents",
                                       params={"tent_ids": [1]}),
            ])
        )

        restored = self._new_engine_from_db()
        self.assertEqual(restored.tents[1].status, "available")

    def test_growth_project_facility_recovery(self):
        """购买餐饮 Lv1 后恢复，等级和余额变化仍存在"""
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_dining_groups = 8
        initial_level = self.engine.facilities["dining"].level
        initial_balance = self.engine.state.balance

        game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "dining_lv1"}),
            ])
        )

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["dining"].level, initial_level + 1)
        self.assertLess(restored.state.balance, initial_balance)

    def test_entertainment_upgrade_recovery(self):
        """购买娱乐 Lv1 后恢复，等级和余额变化仍存在"""
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_paid_entertainment_groups = 8
        initial_level = self.engine.facilities["entertainment"].level
        initial_balance = self.engine.state.balance

        game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "entertainment_lv1"}),
            ])
        )

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["entertainment"].level, initial_level + 1)
        self.assertLess(restored.state.balance, initial_balance)

    def test_dining_food_stock_recovery(self):
        self.engine.state.turn = 3
        guest = NPCGroup(
            id=self.engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            location="campsite",
            economic_level=1,
            spending_habit=1,
            total_satisfaction=60,
        )
        self.engine.npc_pool.append(guest)
        self.engine.state.food_stock = 2
        self.engine.facilities["dining"].level = 1
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [{
            "npc_id": guest.id,
            "planned_day": self.engine.state.day,
            "planned_turn": 3,
            "arrival_status": "arrived",
            "planned_actions": [{
                "action": "dining",
                "status": "pending",
                "planned_turn": 3,
                "menu_key": "standard",
            }],
        }]

        result = self._plan()
        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.food_stock, 0)
        self.assertTrue(self.engine.save_state())

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["dining"].level, 1)
        self.assertEqual(restored.state.food_stock, 0)
        self.assertEqual(restored.state.today_income["dining"], 90)
        self.assertEqual(restored.npc_pool[0].last_dining_day, restored.state.day)


    def test_turn6_food_preorder_recovery_blocks_repeat_same_day(self):
        self.engine.state.turn = 6
        opening_stock = self.engine.state.food_stock

        # Turn 6 日终批处理路径
        result = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="buy_food_package",
                                       params={"package_key": "medium"}),
            ])
        )
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
        self.assertIsNone(state["average_rating"])
        self.assertNotIn("reputation_rate", state)


class ReviewAndSummaryStateApiTests(ApiPersistenceTestCase):
    def test_api_state_exposes_formal_debt_remaining(self):
        self.assertEqual(game_api.get_state()["debt_remaining"], 6000)

        self.engine.state.debt_remaining = 12345
        self.assertEqual(game_api.get_state()["debt_remaining"], 12345)

    def test_api_state_exposes_review_history_and_day1_null_summary(self):
        state = game_api.get_state()

        self.assertEqual(state["review_history"], self.engine.state.review_history)
        self.assertIsNone(state["average_rating"])
        self.assertNotIn("reputation_rate", state)
        self.assertIsNone(state["previous_day_summary"])
        self.assertNotIn("day_start_balance", state)
        self.assertNotIn("pending_reviews", state)

    def test_api_state_exposes_existing_history_and_previous_day_summary(self):
        review = {
            "created_day": 1,
            "rating": 4,
            "npc_id": 7,
            "visit_type": "overnight",
            "group_size": 2,
        }
        summary = {
            "day": 1,
            "income_total": 500,
            "expense_total": 200,
            "net_income": 300,
            "guest_groups_served": 3,
        }
        self.engine.state.day = 2
        self.engine.state.turn = 1
        self.engine.state.review_history = [review]
        self.engine.state.total_reviews = 2
        self.engine.state.total_rating_sum = 9
        self.engine.state.previous_day_summary = summary
        self.engine.state.today_income["campsite"] = 70
        self.engine.state.event_history = [{
            "day": 2,
            "turn": 1,
            "text": "新日事件",
            "kind": "world",
        }]

        state = game_api.get_state()

        self.assertEqual(state["review_history"], [review])
        self.assertEqual(state["average_rating"], 4.5)
        self.assertNotIn("reputation_rate", state)
        self.assertEqual(state["previous_day_summary"], summary)
        self.assertEqual(state["day"], 2)
        self.assertEqual(state["turn"], 1)
        self.assertEqual(state["today_income"]["campsite"], 70)
        self.assertEqual(state["event_history"], self.engine.state.event_history)
        self.assertNotIn("day_start_balance", state)
        self.assertNotIn("pending_reviews", state)


class TurnPlanApiTests(ApiPersistenceTestCase):
    def test_empty_plan_executes_and_advances(self):
        self.engine.state.turn = 2

        result = self._plan()

        self.assertTrue(result["success"])
        self.assertEqual(result["turn"], 3)
        self.assertEqual(set(result), {"success", "day", "turn", "events"})
        self.assertIsNone(self.engine.state.pending_turn_plan)

    def test_one_and_three_actions_can_submit(self):
        self.engine.state.turn = 2
        result = self._plan(
            actions=[game_api.ActionRequest(action="improve_service")]
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["turn"], 3)

        self.engine.state.turn = 2
        self.engine.state.pending_turn_plan = None
        self.engine.state.decisions_left = 3
        result = self._plan(
            actions=[
                game_api.ActionRequest(action="repair_tent", params={"tent_id": 1}),
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="buy_food_package", params={"package_key": "small"}),
            ]
        )
        self.assertTrue(result["success"])

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
                game_api.ActionRequest(action="repair_tent", params={"tent_id": 1}),
                game_api.ActionRequest(action="improve_service"),
                game_api.ActionRequest(action="buy_food_package", params={"package_key": "small"}),
            ]
        )
        self.assertTrue(result["success"])

    def test_turn1_and_turn6_submission_rejected(self):
        for turn in (1, 6):
            self.engine.state.turn = turn
            self.engine.state.pending_turn_plan = None
            self.engine.state.decisions_left = 3
            result = self._plan()
            self.assertFalse(result["success"])
            self.assertEqual(self.engine.state.turn, turn)
            self.assertIsNone(self.engine.state.pending_turn_plan)

    def test_submit_executes_decision_action_and_clears_pending_plan(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "broken"

        result = self._plan(
            actions=[game_api.ActionRequest(action="repair_tent", params={"tent_id": 1})]
        )

        self.assertTrue(result["success"])
        self.assertNotIn("plan_execution", result)
        self.assertEqual(self.engine.state.turn, 3)
        self.assertIsNone(self.engine.state.pending_turn_plan)

    def test_advance_turn_route_remains_available_for_turn1(self):
        self.engine.state.turn = 1

        result = game_api.advance_turn()

        self.assertEqual(result["turn"], 2)

    def test_business_turn_immediate_actions_are_rejected(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[2].status = "broken"
        balance_before = self.engine.state.balance
        decisions_before = self.engine.state.decisions_left

        clean_result = self._action("clean_tents", {"tent_ids": [1]})
        repair_result = self._action("repair_tent", {"tent_id": 2})
        improve_result = self._action("improve_service")

        self.assertFalse(clean_result["success"])
        self.assertFalse(repair_result["success"])
        self.assertFalse(improve_result["success"])
        for result in (clean_result, repair_result, improve_result):
            self.assertIn("/api/turn/plan", result["message"])
        self.assertEqual(self.engine.tents[1].status, "cleaning")
        self.assertEqual(self.engine.tents[2].status, "broken")
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.state.decisions_left, decisions_before)
        return
        self.assertEqual(
            clean_result["message"],
            "请通过 /api/turn/plan 安排下一营业Turn行动。"
        )

    def test_business_turn_direct_food_purchase_is_rejected(self):
        self.engine.state.turn = 2

        result = self._action("buy_food_package", {"package_key": "small"})

        self.assertFalse(result["success"])

    def test_turn6_immediate_management_actions_rejected(self):
        """Turn 6 逐项日终操作返回 day_end_batch_required，不走 /api/day/end"""
        self.engine.state.turn = 6
        self.engine.tents[1].status = "broken"
        self.engine.state.decisions_left = 3

        # Turn 6 逐项 repair_tent 已被拒绝，断言 day_end_batch_required
        with self.assertRaises(game_api.HTTPException) as ctx:
            self._action("repair_tent", {"tent_id": 1})
        self.assertEqual(ctx.exception.detail["error_code"], "day_end_batch_required")

    def test_turn6_buy_food_package_works_once(self):
        self.engine.state.turn = 6
        opening_stock = self.engine.state.food_stock

        # Turn 6 日终批处理：第一次成功，重复提交被拒
        first = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="buy_food_package",
                                       params={"package_key": "small"}),
            ])
        )
        self.assertTrue(first["success"])
        self.assertTrue(first["day_end_completed"])

        second = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="buy_food_package",
                                       params={"package_key": "medium"}),
            ])
        )
        self.assertFalse(second["success"])
        self.assertEqual(second["error_code"], "day_end_already_completed")
        self.assertEqual(
            self.engine.state.food_stock,
            opening_stock + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"],
        )

class McpTurnPlanTests(ApiPersistenceTestCase):
    def test_mcp_state_turn1_omits_turn_specific_and_empty_fields(self):
        self.engine.state.turn = 1
        self.engine.state.today_events = []

        state = game_api.mcp_state()

        for field in (
            "decisions_left",
            "planning_available",
            "plan_submitted",
            "plan_target_turn",
            "turn_plan",
            "day_end_completed",
            "hot_spring",
            "day_campsite",
            "next_turn_checkout_tents",
            "today_events",
            "turn_alerts",
        ):
            self.assertNotIn(field, state)
        self.assertIn("food_stock", state)
        self.assertIn("today_income", state)

    def test_mcp_state_turn6_omits_income_and_food_stock(self):
        self.engine.state.turn = 6

        state = game_api.mcp_state()

        self.assertIn("day_end_completed", state)
        self.assertNotIn("today_income", state)
        self.assertNotIn("food_stock", state)
        for field in ("day", "turn", "balance", "average_rating", "tents", "facilities", "reservations", "greenery"):
            self.assertIn(field, state)

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
        self.assertNotIn("phase", state)
        self.assertNotIn("available_actions", state)

        self.assertIn("reservations", state)
        for field in [
            "hot_spring",
            "day_campsite",
            "planning_available",
            "plan_submitted",
            "plan_target_turn",
            "turn_plan",
            "next_turn_checkout_tents",
        ]:
            self.assertNotIn(field, state)
        self.assertIn("arrival_plan", state)

        self.assertEqual(self.engine.facilities["greenery"].greenery_satisfaction, 6.5)
        self.assertTrue(self.engine.state.greenery_processed_today)
        self.assertEqual(self.engine.state.day, day_before)
        self.assertEqual(self.engine.state.turn, turn_before)

    def test_mcp_state_exposes_turn_plan_flags_only(self):
        self.engine.state.turn = 2

        state = game_api.mcp_state()
        self.assertTrue(state["planning_available"])
        self.assertFalse(state["plan_submitted"])
        self.assertNotIn("plan_target_turn", state)
        self.assertNotIn("turn_plan", state)
        self.assertNotIn("pending_turn_plan", state)
        self.assertNotIn("free_actions", state)
        self.assertNotIn("actions", state)

        self._plan()
        state = game_api.mcp_state()
        self.assertTrue(state["planning_available"])
        self.assertFalse(state["plan_submitted"])
        self.assertNotIn("plan_target_turn", state)
        self.assertNotIn("turn_plan", state)
        self.assertNotIn("next_turn_checkout_tents", state)

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

        # Turn 6 日终批处理：购买 dining_lv1 再购买 dining_lv2
        first = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "dining_lv1"}),
            ])
        )
        self.assertTrue(first["success"])
        self.assertEqual(self.engine.facilities["dining"].level, 1)

        # 日终提交已自动开启下一天；切回 Turn 6 模拟下一次日终。
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False

        second = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "dining_lv2"}),
            ])
        )
        self.assertTrue(second["success"])
        self.assertEqual(self.engine.facilities["dining"].level, 2)

        # 日终提交已自动开启下一天；回 Turn 6 尝试第三次升级（dining_lv2 已购买，不可重复）。
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False
        balance_before_third = self.engine.state.balance

        third = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "dining_lv2"}),
            ])
        )
        self.assertTrue(third["success"])  # 批处理本身成功
        self.assertFalse(third["results"][0]["success"])  # 但购买项失败
        self.assertEqual(self.engine.facilities["dining"].level, 2)
        self.assertGreaterEqual(self.engine.state.balance, balance_before_third)

    def test_entertainment_upgrade_reaches_lv2_and_then_stops_without_charge(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.state.successful_paid_entertainment_groups = 32

        # Turn 6 日终批处理：购买 entertainment_lv1 再购买 entertainment_lv2
        first = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "entertainment_lv1"}),
            ])
        )
        self.assertTrue(first["success"])
        self.assertEqual(self.engine.facilities["entertainment"].level, 1)

        # 日终提交已自动开启下一天。
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False

        second = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "entertainment_lv2"}),
            ])
        )
        self.assertTrue(second["success"])
        self.assertEqual(self.engine.facilities["entertainment"].level, 2)

        # 第三次尝试升级（entertainment_lv2 已购买，不可重复）
        # 日终提交已自动开启下一天。
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = False
        balance_before_third = self.engine.state.balance

        third = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "entertainment_lv2"}),
            ])
        )
        self.assertTrue(third["success"])
        self.assertFalse(third["results"][0]["success"])
        self.assertEqual(self.engine.facilities["entertainment"].level, 2)
        self.assertGreaterEqual(self.engine.state.balance, balance_before_third)

    def test_greenery_lv2_upgrade_still_fails_without_charge(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        self.engine.facilities["greenery"].level = 2
        balance_before = self.engine.state.balance

        # Turn 6 日终批处理：greenery_lv2 需要 required_level=1（当前 level=2 不满足）
        # greenery_lv2 已购买或前置不满足，结果应为失败
        result = game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="purchase_growth_project",
                                       params={"project_id": "greenery_lv2"}),
            ])
        )

        self.assertTrue(result["success"])  # 批处理整体成功
        self.assertFalse(result["results"][0]["success"])  # 购买项失败
        self.assertEqual(self.engine.facilities["greenery"].level, 2)
        self.assertGreaterEqual(self.engine.state.balance, balance_before)

    def test_mcp_actions_switch_between_plan_and_turn6_management(self):
        self.engine.state.turn = 2
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]
        self.assertIn("execute_turn_plan", action_names)
        self.assertNotIn("clean_tents", action_names)
        self.assertNotIn("repair_tent", action_names)
        self.assertNotIn("improve_service", action_names)

        self._plan()
        self.engine.state.today_conflict_event = {"status": "no_event"}
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]
        self.assertIn("execute_turn_plan", action_names)
        self.assertNotIn("advance_turn", action_names)

        self.engine.state.turn = 6
        self.engine.state.pending_turn_plan = None
        self.engine.tents[1].status = "broken"
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[2].status = "cleaning"
        actions = game_api.mcp_available_actions()["available_actions"]
        action_names = [item["action"] for item in actions]
        self.assertIn("submit_day_end_actions", action_names)
        self.assertNotIn("execute_turn_plan", action_names)
        self.assertNotIn("repair_tent", action_names)
        self.assertNotIn("clean_tents", action_names)
        self.assertNotIn("new_day", action_names)

    def test_mcp_actions_expose_start_next_day_in_recovery_state(self):
        self.engine.state.turn = 6
        self.engine.state.day_end_completed = True

        actions = game_api.mcp_available_actions()

        self.assertTrue(actions["day_end_completed"])
        self.assertEqual(actions["available_actions"][0]["action"], "start_next_day")
        self.assertEqual(actions["available_actions"][0]["endpoint"], "/api/day/start")

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
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "submit_day_end_actions")
        food_actions = [
            item for item in actions[0]["day_end_action_candidates"]
            if item["action"] == "buy_food_package"
        ]

        self.assertEqual(len(food_actions), 3)
        self.assertEqual(
            sorted(item["params"]["package_key"] for item in food_actions),
            sorted(CampingPlazaEngine.FOOD_PACKAGES.keys()),
        )
        for item in food_actions:
            package = CampingPlazaEngine.FOOD_PACKAGES[item["params"]["package_key"]]
            self.assertEqual(item["portions"], package["portions"])
            self.assertEqual(item["cost"], package["price"])
            self.assertTrue(item["enabled"])

    def test_mcp_actions_hide_turn6_food_preorder_after_success(self):
        self.engine.state.turn = 6
        # Turn 6 日终批处理路径
        game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=[
                game_api.ActionRequest(action="buy_food_package",
                                       params={"package_key": "small"}),
            ])
        )

        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "start_next_day")

    def test_mcp_plan_description_mentions_food_purchase_action(self):
        self.engine.state.turn = 2

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(item for item in actions if item["action"] == "execute_turn_plan")

        self.assertIn("buy_food_package", submit_action["description"])
        self.assertIn("package_key", submit_action["description"])

    def test_turn2_submit_plan_includes_repair_candidates_when_broken(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "broken"

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(
            item for item in actions if item["action"] == "execute_turn_plan"
        )

        self.assertIn("repair_candidates", submit_action)
        candidates = submit_action["repair_candidates"]
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["action"], "repair_tent")
        self.assertEqual(candidates[0]["params"], {"tent_id": 1})
        self.assertIn("100", candidates[0]["description"])

    def test_turn2_no_repair_candidates_without_broken(self):
        self.engine.state.turn = 2

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(
            item for item in actions if item["action"] == "execute_turn_plan"
        )

        self.assertNotIn("repair_candidates", submit_action)

    def test_turn2_multiple_broken_has_multiple_repair_candidates(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "broken"
        self.engine.tents[3].is_unlocked = True
        self.engine.tents[3].status = "broken"

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(
            item for item in actions if item["action"] == "execute_turn_plan"
        )

        candidates = submit_action["repair_candidates"]
        self.assertEqual(len(candidates), 2)
        tent_ids = sorted(c["params"]["tent_id"] for c in candidates)
        self.assertEqual(tent_ids, [1, 3])

    def test_turn6_repair_tent_still_direct_action(self):
        self.engine.state.turn = 6
        self.engine.tents[1].status = "broken"

        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "submit_day_end_actions")
        self.assertNotIn("repair_candidates", actions[0])
        candidates = actions[0]["day_end_action_candidates"]
        repair = next(
            candidate for candidate in candidates if candidate["action"] == "repair_tent"
        )
        self.assertEqual(repair["params"]["tent_id"], 1)
        self.assertEqual(repair["cost"], CampingPlazaEngine.REPAIR_COST)
        self.assertTrue(repair["enabled"])
        self.assertEqual(repair["reason"], "")

    def test_turn2_repair_candidates_present_even_when_balance_zero(self):
        """余额不足时维修候选仍出现，执行时才会失败"""
        self.engine.state.turn = 2
        self.engine.state.balance = 0
        self.engine.tents[1].status = "broken"

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(
            item for item in actions if item["action"] == "execute_turn_plan"
        )

        self.assertIn("repair_candidates", submit_action)
        self.assertEqual(
            submit_action["repair_candidates"][0]["params"], {"tent_id": 1}
        )

    def test_turn2_repair_candidates_disappear_after_all_repaired(self):
        """修好全部 broken 后维修候选消失"""
        self.engine.state.turn = 2
        self.engine.state.today_conflict_event = {"status": "no_event"}
        self.engine.tents[1].status = "broken"
        self.engine.tents[3].is_unlocked = True
        self.engine.tents[3].status = "broken"

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(
            item for item in actions if item["action"] == "execute_turn_plan"
        )
        self.assertEqual(len(submit_action["repair_candidates"]), 2)

        # 修好全部 broken
        self.engine.tents[1].status = "available"
        self.engine.tents[3].status = "available"

        actions = game_api.mcp_available_actions()["available_actions"]
        submit_action = next(
            item for item in actions if item["action"] == "execute_turn_plan"
        )
        self.assertNotIn("repair_candidates", submit_action)


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
        self.assertEqual(game_api.mcp_state()["hot_spring"], expected)
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
            game_api.mcp_state()
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
        self.assertEqual(game_api.mcp_state()["day_campsite"], expected)
        self.assertEqual(game_api.mcp_state()["day_campsite"], expected)

    def test_read_only_outputs_do_not_save_or_mutate_and_keep_hot_spring(self):
        self.engine.state.day_campsite_groups_served = 6
        self._add_pending_day_reservation(npc_id=9002)
        plan_before = list(self.engine.state.today_arrival_plan)
        hot_spring_before = game_api.get_state()["hot_spring"]

        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.get_state()
            game_api.mcp_state()
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
            game_api.mcp_state()["arrival_plan"],
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
                game_api.mcp_state()
                game_api.mcp_state()
                save_mock.assert_not_called()
                ensure_mock.assert_not_called()

        self.assertEqual(self.engine.state.today_arrival_plan, plan_before)
        self.assertEqual(self.engine.state.today_arrival_plan_day, self.engine.state.day)

        state = game_api.get_state()
        self.assertIn("hot_spring", state)
        self.assertIn("day_campsite", state)


class McpGrowthActionTests(ApiPersistenceTestCase):
    """Turn 6 日终批处理模式：统一候选携带可购买成长项目。"""

    def _growth_purchase_actions(self, actions):
        """从 submit_day_end_actions 入口中提取成长项目候选。"""
        entry = next(
            (a for a in actions if a["action"] == "submit_day_end_actions"), None
        )
        if entry is None:
            return []
        return [
            candidate for candidate in entry.get("day_end_action_candidates", [])
            if candidate["action"] == "purchase_growth_project"
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
        self.assertEqual(tent2["cost"], 600)
        self.assertTrue(tent2["enabled"])

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
        # hot_spring 需要至少 5 个普通成长节点，默认未满足经营条件
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
        self.assertTrue(any(a["action"] == "execute_turn_plan" for a in actions))

    def test_turn6_keeps_existing_actions(self):
        self.engine.state.turn = 6
        self.engine.state.day = 7
        self.engine.state.balance = 10000

        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual(len(actions), 1)
        entry = actions[0]
        self.assertEqual(entry["action"], "submit_day_end_actions")

        candidates = entry["day_end_action_candidates"]
        purchase_ids = [
            candidate["params"]["project_id"]
            for candidate in candidates
            if candidate["action"] == "purchase_growth_project"
        ]
        self.assertTrue(purchase_ids)
        self.assertIn("manage_greenery", [candidate["action"] for candidate in candidates])
        self.assertNotIn("upgrade_facility", entry.get("action", ""))

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
        self.assertEqual(dining1["cost"], 700)
        self.assertTrue(dining1["enabled"])

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
            ) as purchase_mock:
                game_api.mcp_available_actions()
                save_mock.assert_not_called()
                purchase_mock.assert_not_called()

        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.get_growth_progress(), progress_before)
        self.assertEqual(self.engine.tents[2].is_unlocked, tent2_unlocked_before)
        self.assertEqual(self.engine.facilities["dining"].level, dining_level_before)


class ActionRequestSemanticErrorTests(ApiPersistenceTestCase):
    """POST /api/action 的请求语义错误应返回 400 + 稳定 error_code + 中文 message"""

    def _assert_semantic_error(self, action, params, expected_code, expected_message,
                               engine_method):
        with mock.patch.object(self.engine, engine_method) as engine_mock:
            with mock.patch.object(self.engine, "save_state") as save_mock:
                with self.assertRaises(game_api.HTTPException) as ctx:
                    self._action(action, params)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            ctx.exception.detail,
            {"error_code": expected_code, "message": expected_message},
        )
        engine_mock.assert_not_called()
        save_mock.assert_not_called()

    def test_repair_tent_missing_tent_id(self):
        self.engine.state.turn = 6  # 日终阶段已禁止逐项 repair_tent
        self._assert_semantic_error(
            "repair_tent", {}, "day_end_batch_required",
            "Turn 6 日终阶段请使用 /api/day/end 统一提交经营清单，不再支持逐项调用 repair_tent",
            "repair_tent"
        )

    def test_removed_upgrade_facility_is_unknown_action(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            with self.assertRaises(game_api.HTTPException) as ctx:
                self._action("upgrade_facility", {"facility_name": "dining"})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            ctx.exception.detail,
            {"error_code": "unknown_action", "message": "未知操作: upgrade_facility"},
        )
        save_mock.assert_not_called()

    def test_buy_food_package_missing_package_key(self):
        self.engine.state.turn = 6  # 日终阶段已禁止逐项 buy_food_package
        self._assert_semantic_error(
            "buy_food_package", {}, "day_end_batch_required",
            "Turn 6 日终阶段请使用 /api/day/end 统一提交经营清单，不再支持逐项调用 buy_food_package",
            "buy_food_package",
        )

    def test_purchase_growth_project_invalid_project_id(self):
        for params in (None, {}, {"project_id": 12}, {"project_id": ""}, {"project_id": "  "}):
            with self.subTest(params=params):
                with mock.patch.object(
                    self.engine, "purchase_growth_project"
                ) as purchase_mock:
                    with mock.patch.object(self.engine, "save_state") as save_mock:
                        with self.assertRaises(game_api.HTTPException) as ctx:
                            self._action("purchase_growth_project", params)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(
                    ctx.exception.detail,
                    {"error_code": "invalid_project_id", "message": "缺少有效的project_id参数"},
                )
                purchase_mock.assert_not_called()
                save_mock.assert_not_called()

    def test_unknown_action(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            with self.assertRaises(game_api.HTTPException) as ctx:
                self._action("definitely_not_an_action", {})
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(
            ctx.exception.detail,
            {"error_code": "unknown_action", "message": "未知操作: definitely_not_an_action"},
        )
        save_mock.assert_not_called()

    def test_semantic_errors_do_not_mutate_state(self):
        self.engine.state.turn = 6  # Turn 6 逐项 repair/buy_food 均被拒绝
        balance_before = self.engine.state.balance
        day_before = self.engine.state.day
        turn_before = self.engine.state.turn
        for action, params in [
            ("repair_tent", {}),
            ("buy_food_package", {}),
            ("purchase_growth_project", {"project_id": ""}),
            ("not_an_action", {}),
        ]:
            with self.assertRaises(game_api.HTTPException):
                self._action(action, params)
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.state.day, day_before)
        self.assertEqual(self.engine.state.turn, turn_before)


class TurnPlanStateSummaryTests(ApiPersistenceTestCase):
    """mcp/state 应提供已提交 Turn Plan 的只读安全摘要"""

    def test_no_turn_plan_is_omitted(self):
        self.engine.state.pending_turn_plan = None
        self.engine.state.turn = 2

        state = game_api.mcp_state()

        self.assertNotIn("turn_plan", state)
        self.assertFalse(state["plan_submitted"])
        self.assertNotIn("plan_target_turn", state)
        self.assertTrue(state["planning_available"])

    def test_full_safe_summary(self):
        self.engine.state.day = 7
        self.engine.state.turn = 3
        self.engine.state.pending_turn_plan = {
            "target_day": 7,
            "target_turn": 3,
            "free_actions": [
                {"action": "clean_tents", "tent_ids": [1, 2]},
            ],
            "actions": [
                {"action": "repair_tent", "tent_id": 1},
                {"action": "improve_service"},
                {"action": "buy_food_package", "package_key": "basic"},
            ],
        }

        state = game_api.mcp_state()

        self.assertEqual(
            state["turn_plan"],
            {
                "target_day": 7,
                "target_turn": 3,
                "free_actions": [
                    {"action": "clean_tents", "params": {"tent_ids": [1, 2]}},
                ],
                "decision_actions": [
                    {"action": "repair_tent", "params": {"tent_id": 1}},
                    {"action": "improve_service"},
                    {"action": "buy_food_package", "params": {"package_key": "basic"}},
                ],
            },
        )

    def test_whitelist_filters_unknown_fields_and_actions(self):
        self.engine.state.day = 1
        self.engine.state.turn = 3
        self.engine.state.pending_turn_plan = {
            "target_day": 1,
            "target_turn": 3,
            "free_actions": [
                {
                    "action": "clean_tents",
                    "tent_ids": [3],
                    "secret": "x",
                    "raw_params": {"a": 1},
                    "internal_note": "n",
                },
                {"action": "mystery_action", "thing": 1},
                "not_a_dict",
            ],
            "actions": [],
        }

        state = game_api.mcp_state()

        self.assertEqual(
            state["turn_plan"]["free_actions"],
            [{"action": "clean_tents", "params": {"tent_ids": [3]}}],
        )
        self.assertEqual(state["turn_plan"]["decision_actions"], [])

    def test_does_not_expose_raw_plan(self):
        self.engine.state.pending_turn_plan = {
            "target_day": 1,
            "target_turn": 3,
            "free_actions": [
                {"action": "clean_tents", "tent_ids": [1], "secret": "SENSITIVE"},
            ],
            "actions": [],
        }

        state = game_api.mcp_state()

        self.assertNotIn("pending_turn_plan", state)
        self.assertNotIn("free_actions", state)
        self.assertNotIn("actions", state)
        dumped = json.dumps(state, ensure_ascii=False)
        self.assertNotIn("SENSITIVE", dumped)
        self.assertNotIn("secret", dumped)

    def test_no_shared_mutable_reference(self):
        self.engine.state.day = 1
        self.engine.state.turn = 3
        self.engine.state.pending_turn_plan = {
            "target_day": 1,
            "target_turn": 3,
            "free_actions": [
                {"action": "clean_tents", "tent_ids": [1, 2]},
            ],
            "actions": [],
        }

        summary = game_api.mcp_state()["turn_plan"]
        summary["free_actions"][0]["params"]["tent_ids"].append(99)

        self.assertEqual(
            self.engine.state.pending_turn_plan["free_actions"][0]["tent_ids"],
            [1, 2],
        )

    def test_turn_plan_read_only(self):
        plan_before = {
            "target_day": 1,
            "target_turn": 3,
            "free_actions": [
                {"action": "clean_tents", "tent_ids": [1]},
            ],
            "actions": [],
        }
        self.engine.state.pending_turn_plan = dict(plan_before)
        day_before = self.engine.state.day
        turn_before = self.engine.state.turn
        balance_before = self.engine.state.balance
        decisions_before = self.engine.state.decisions_left

        with mock.patch.object(self.engine, "save_state") as save_mock:
            with mock.patch.object(
                game_api, "_normalize_turn_plan_actions"
            ) as normalize_mock, mock.patch.object(
                self.engine, "submit_turn_plan"
            ) as submit_mock, mock.patch.object(
                self.engine, "advance_turn"
            ) as advance_mock, mock.patch.object(
                self.engine, "_execute_pending_turn_plan"
            ) as execute_mock:
                game_api.mcp_state()
                save_mock.assert_not_called()
                normalize_mock.assert_not_called()
                submit_mock.assert_not_called()
                advance_mock.assert_not_called()
                execute_mock.assert_not_called()

        self.assertEqual(self.engine.state.pending_turn_plan, plan_before)
        self.assertEqual(self.engine.state.day, day_before)
        self.assertEqual(self.engine.state.turn, turn_before)
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.state.decisions_left, decisions_before)


class DayEndApiTests(ApiPersistenceTestCase):
    """Turn 6 批处理 API 路由：/api/day/end 与 /api/day/start"""

    def _day_end(self, actions=None):
        """构造 DayEndRequest 并调用 game_api.submit_day_end"""
        return game_api.submit_day_end(
            game_api.DayEndRequest(day_end_actions=actions or [])
        )

    def _make_action(self, action, params=None):
        return game_api.ActionRequest(action=action, params=params)

    def _reach_turn6(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 1000

    def test_empty_day_end_actions_completes(self):
        self._reach_turn6()
        result = self._day_end()
        self.assertTrue(result["success"])
        self.assertEqual(result["action_execution_status"], "no_actions")
        self.assertEqual(result["succeeded_count"], 0)
        self.assertEqual(result["failed_count"], 0)
        self.assertTrue(result["day_end_completed"])
        self.assertEqual(result["day"], 1)
        self.assertEqual(result["turn"], 6)
        self.assertEqual(result["balance"], self.engine.state.balance)
        self.assertEqual(result["next_action"], "start_next_day")
        self.assertEqual(result["next_endpoint"], "/api/day/start")
        self.assertEqual(self.engine.state.day, 1)
        self.assertEqual(self.engine.state.turn, 6)
        self.assertTrue(self.engine.state.day_end_completed)

    def test_turn6_budget_hint_is_shared_by_human_and_mcp_actions(self):
        self._reach_turn6()
        human_actions = game_api.get_human_actions()
        mcp_actions = game_api.mcp_available_actions()

        expected = "提示：如选择还款，还款金额与所选经营决策项费用合计不得超过当前余额。"
        self.assertEqual(human_actions["day_end_budget_hint"], expected)
        self.assertEqual(mcp_actions["day_end_budget_hint"], expected)
        self.assertNotIn("其他可选日终行动", expected)
        self.assertNotIn("先还款", expected)
        self.assertNotIn("优先还款", expected)

        repay = next(
            item for item in human_actions["day_end_action_candidates"]
            if item["action"] == "repay_debt"
        )
        self.assertIsNone(repay["params"]["amount"])
        self.assertEqual(repay["min_amount"], 1)
        self.assertEqual(repay["max_amount"], self.engine.state.balance)

    def test_missing_day_end_actions_is_rejected_without_processing(self):
        self._reach_turn6()
        before = (
            self.engine.state.balance,
            self.engine.state.debt_remaining,
            self.engine.facilities["greenery"].level,
            self.engine.state.food_stock,
            self.engine.state.day_end_completed,
        )

        with self.assertRaises(game_api.HTTPException) as context:
            game_api.submit_day_end(game_api.DayEndRequest())

        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(context.exception.detail["error_code"], "missing_day_end_actions")
        self.assertIn("day_end_actions", context.exception.detail["message"])
        self.assertEqual(
            (
                self.engine.state.balance,
                self.engine.state.debt_remaining,
                self.engine.facilities["greenery"].level,
                self.engine.state.food_stock,
                self.engine.state.day_end_completed,
            ),
            before,
        )

    def test_actions_is_not_a_silent_day_end_alias_and_retry_is_allowed(self):
        self._reach_turn6()
        with self.assertRaises(ValidationError) as context:
            game_api.DayEndRequest(
                actions=[self._make_action("manage_greenery", {"action": "maintain"})]
            )

        self.assertEqual(context.exception.errors()[0]["type"], "extra_forbidden")
        self.assertFalse(self.engine.state.day_end_completed)

        retry = self._day_end([])
        self.assertTrue(retry["success"])
        self.assertTrue(retry["day_end_completed"])

    def test_mixed_success_failure_results_preserved_and_returns_200(self):
        self._reach_turn6()
        self.engine.state.food_stock = 0
        result = self._day_end([
            self._make_action("buy_food_package", {"package_key": "small"}),
            self._make_action("repair_tent", {"tent_id": 1}),  # 帐篷未 broken，失败
            self._make_action("manage_greenery", {"action": "maintain"}),
        ])
        self.assertTrue(result["success"])
        self.assertEqual(result["action_execution_status"], "partial_success")
        self.assertEqual(result["succeeded_count"], 2)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(result["results"]), 3)
        self.assertTrue(result["results"][0]["success"])   # buy_food_package
        self.assertFalse(result["results"][1]["success"])  # repair_tent 未损坏
        self.assertTrue(result["results"][1]["error_code"])
        self.assertTrue(result["results"][1]["message"])
        self.assertTrue(result["results"][2]["success"])   # manage_greenery
        self.assertEqual(self.engine.state.day, 1)
        self.assertEqual(self.engine.state.turn, 6)
        # 单项业务失败仍保留在 results，整体 200

    def test_day_end_rejects_multiple_food_packages_before_processing(self):
        self._reach_turn6()
        balance_before = self.engine.state.balance
        stock_before = self.engine.state.food_stock
        preorder_day_before = self.engine.state.last_food_preorder_day

        result = self._day_end([
            self._make_action("buy_food_package", {"package_key": "small"}),
            self._make_action("buy_food_package", {"package_key": "medium"}),
        ])

        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "duplicate_food_preorder")
        self.assertEqual(self.engine.state.day, 1)
        self.assertEqual(self.engine.state.turn, 6)
        self.assertEqual(self.engine.state.balance, balance_before)
        self.assertEqual(self.engine.state.food_stock, stock_before)
        self.assertEqual(self.engine.state.last_food_preorder_day, preorder_day_before)
        self.assertFalse(self.engine.state.day_end_completed)

    def test_day_end_allows_one_food_package_with_other_actions(self):
        self._reach_turn6()
        result = self._day_end([
            self._make_action("buy_food_package", {"package_key": "small"}),
            self._make_action("manage_greenery", {"action": "maintain"}),
        ])

        self.assertTrue(result["success"])
        self.assertEqual(result["action_execution_status"], "all_succeeded")
        self.assertEqual(result["succeeded_count"], 2)
        self.assertEqual(result["failed_count"], 0)
        self.assertTrue(result["results"][0]["success"])
        self.assertEqual(result["results"][0]["price"], 80)
        self.assertEqual(result["results"][0]["portions"], 4)
        self.assertTrue(result["results"][1]["success"])
        self.assertTrue(self.engine.state.day_end_completed)
        self.assertEqual(self.engine.state.turn, 6)

    def test_all_failed_day_end_actions_have_summary_and_reasons(self):
        self._reach_turn6()
        result = self._day_end([
            self._make_action("repair_tent", {"tent_id": 1}),
        ])

        self.assertTrue(result["success"])
        self.assertEqual(result["action_execution_status"], "all_failed")
        self.assertEqual(result["succeeded_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertFalse(result["results"][0]["success"])
        self.assertTrue(result["results"][0]["error_code"])
        self.assertTrue(result["results"][0]["message"])
        self.assertTrue(result["day_end_completed"])
        self.assertEqual(result["day"], 1)
        self.assertEqual(result["turn"], 6)

    def test_day_end_allows_single_food_package(self):
        self._reach_turn6()
        stock_before = self.engine.state.food_stock

        result = self._day_end([
            self._make_action("buy_food_package", {"package_key": "small"}),
        ])

        self.assertTrue(result["success"])
        self.assertTrue(result["results"][0]["success"])
        self.assertEqual(
            self.engine.state.food_stock,
            stock_before + CampingPlazaEngine.FOOD_PACKAGES["small"]["portions"],
        )

    def test_non_turn6_rejected(self):
        self.engine.state.turn = 2
        result = self._day_end()
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "day_end_not_available")
        self.assertFalse(self.engine.state.day_end_completed)

    def test_repeat_submission_rejected(self):
        self._reach_turn6()
        first = self._day_end()
        self.assertTrue(first["success"])
        second = self._day_end()
        self.assertFalse(second["success"])
        self.assertEqual(second["error_code"], "day_end_already_completed")
        self.assertEqual(self.engine.state.day, 1)
        self.assertEqual(self.engine.state.turn, 6)

    def test_start_before_completion_rejected(self):
        self._reach_turn6()
        result = game_api.start_next_day()
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "day_end_not_completed")
        self.assertEqual(self.engine.state.day, 1)
        self.assertEqual(self.engine.state.turn, 6)

    def test_start_after_completion_advances(self):
        self._reach_turn6()
        self.engine.submit_day_end_actions([])
        result = game_api.start_next_day()
        self.assertTrue(result["success"])
        self.assertEqual(result["day"], 2)
        self.assertEqual(result["turn"], 1)
        self.assertEqual(self.engine.state.day, 2)
        self.assertEqual(self.engine.state.turn, 1)
        self.assertFalse(self.engine.state.day_end_completed)

    def test_day_end_submission_runs_management_and_growth_before_confirmation(self):
        self._reach_turn6()
        self.engine.state.day = 2
        self.engine.state.balance = 2000

        result = self._day_end([
            self._make_action("manage_greenery", {"action": "maintain"}),
            self._make_action("purchase_growth_project", {"project_id": "tent_2"}),
        ])

        self.assertTrue(result["success"])
        self.assertTrue(all(item["success"] for item in result["results"]))
        self.assertTrue(self.engine.tents[2].is_unlocked)
        self.assertEqual(self.engine.state.day, 2)
        self.assertEqual(self.engine.state.turn, 6)
        self.assertTrue(self.engine.state.day_end_completed)

    def test_save_and_restore_after_day_end_submission_waits_for_confirmation(self):
        self._reach_turn6()
        self._day_end()
        self.assertTrue(self.engine.state.day_end_completed)

        restored = self._new_engine_from_db()
        self.assertEqual(restored.state.day, 1)
        self.assertEqual(restored.state.turn, 6)
        self.assertTrue(restored.state.day_end_completed)

        # 恢复后不能重复执行同一日的日终提交，但可以确认跨日。
        game_api.engine = restored
        dup = game_api.submit_day_end(game_api.DayEndRequest(day_end_actions=[]))
        self.assertFalse(dup["success"])
        self.assertEqual(dup["error_code"], "day_end_already_completed")
        next_day = game_api.start_next_day()
        self.assertTrue(next_day["success"])
        self.assertEqual(restored.state.day, 2)
        self.assertEqual(restored.state.turn, 1)


class EventHistoryStateOutputTests(ApiPersistenceTestCase):
    def test_api_state_exposes_event_history(self):
        self.engine._append_event_history(1, 2, "测试经营事件", "world")

        state = game_api.get_state()

        entry = state["event_history"][-1]
        self.assertEqual(
            {key: entry[key] for key in ("day", "turn", "text", "kind")},
            {"day": 1, "turn": 2, "text": "测试经营事件", "kind": "world"},
        )
        self.assertIsInstance(entry["sequence"], int)
        self.assertEqual(entry["event_type"], "legacy")
        self.assertEqual(entry["guest_ids"], [])
        self.assertEqual(entry["data"], {})


class CampsiteSlotStateOutputTests(ApiPersistenceTestCase):
    def test_api_state_exposes_day_guest_campsite_slot(self):
        self.engine.npc_pool.append(NPCGroup(
            id=901,
            group_size=2,
            visit_type="day",
            location="campsite",
            campsite_slot=6,
        ))

        state = game_api.get_state()

        self.assertEqual(state["active_npcs"], [{
            "id": 901,
            "group_size": 2,
            "visit_type": "day",
            "arrival_turn": 0,
            "location": "campsite",
            "campsite_slot": 6,
            "total_satisfaction": 60,
            "has_left": False,
            "review_left": False,
            "review_rating": 0,
            "visit_count": 1,
            "last_visit_day": 0,
            "is_reserved": False,
            "paid": False,
        }])


class WriteRequestValidationTests(ApiPersistenceTestCase):
    """正式写接口的请求结构校验必须在引擎执行前完成。"""

    def setUp(self):
        super().setUp()
        self.client = TestClient(game_api.app)
        self.addCleanup(self.client.close)

    def _state_marker(self):
        return (
            self.engine.state.day,
            self.engine.state.turn,
            self.engine.state.balance,
            self.engine.state.debt_remaining,
            self.engine.state.day_end_completed,
            self.engine.state.pending_turn_plan,
            self.engine.state.greenery_processed_today,
        )

    def _assert_invalid_request(self, response, error_code):
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"]["error_code"], error_code)

    def test_write_models_reject_unknown_top_level_fields(self):
        requests = [
            ("/api/session", {"unexpected": True}),
            ("/api/turn/advance", {"unexpected": True}),
            ("/api/turn/plan", {"unexpected": True}),
            ("/api/day/end", {"day_end_actions": [], "unexpected": True}),
            ("/api/day/start", {"unexpected": True}),
            ("/api/action", {"action": "advance_turn", "unexpected": True}),
        ]

        for path, payload in requests:
            with self.subTest(path=path):
                marker = self._state_marker()
                response = self.client.post(path, json=payload)
                self.assertEqual(response.status_code, 422, response.text)
                errors = response.json()["detail"]
                self.assertTrue(any(error["type"] == "extra_forbidden" for error in errors))
                self.assertEqual(self._state_marker(), marker)

    def test_api_action_rejects_unknown_action_and_invalid_params_before_execution(self):
        cases = [
            ({"action": "unknown_action"}, "unknown_action"),
            ({"action": "repair_tent", "params": {"tent_id": "abc"}}, "invalid_action_param"),
            ({"action": "clean_tents", "params": {"tent_ids": ["abc"]}}, "invalid_action_param"),
            ({"action": "repair_tent", "params": {"tent_id": 1, "whatever": 123}}, "unknown_action_param"),
            ({"action": "repair_tent", "params": {}}, "missing_action_param"),
            ({"action": "purchase_growth_project", "params": {"project_id": 123}}, "invalid_project_id"),
        ]

        for payload, error_code in cases:
            with self.subTest(payload=payload):
                marker = self._state_marker()
                response = self.client.post("/api/action", json=payload)
                self._assert_invalid_request(response, error_code)
                self.assertEqual(self._state_marker(), marker)

    def test_nested_action_fields_are_rejected_and_valid_action_still_executes(self):
        response = self.client.post(
            "/api/turn/plan",
            json={"actions": [{"action": "improve_service", "unexpected": True}]},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"][0]["type"], "extra_forbidden")

        response = self.client.post("/api/action", json={"action": "advance_turn"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.engine.state.turn, 2)

    def test_turn_plan_rejects_unknown_actions_and_params_before_execution(self):
        self.engine.state.turn = 2
        cases = [
            ({"actions": [{"action": "unknown_action", "params": {}}]}, "unknown_turn_plan_action"),
            ({"actions": [{"action": "repair_tent", "params": {"tent_id": 1, "whatever": 1}}]}, "unknown_action_param"),
            ({"actions": [{"action": "repair_tent", "params": {}}]}, "missing_action_param"),
        ]

        for payload, error_code in cases:
            with self.subTest(payload=payload):
                marker = self._state_marker()
                response = self.client.post("/api/turn/plan", json=payload)
                self._assert_invalid_request(response, error_code)
                self.assertEqual(self._state_marker(), marker)

    def test_day_end_rejects_invalid_actions_and_params_before_any_execution(self):
        self.engine.state.turn = 6
        self.engine.facilities["greenery"].level = 1
        cases = [
            ([
                {"action": "manage_greenery", "params": {"action": "maintain"}},
                {"action": "unknown_action", "params": {}},
            ], "unknown_day_end_action"),
            ([{"action": "repair_tent", "params": {"tent_id": 1, "whatever": 1}}], "unknown_action_param"),
            ([{"action": "repair_tent", "params": {"tent_id": "abc"}}], "invalid_action_param"),
            ([{"action": "buy_food_package", "params": {}}], "missing_action_param"),
        ]

        for actions, error_code in cases:
            with self.subTest(actions=actions):
                marker = self._state_marker()
                response = self.client.post("/api/day/end", json={"day_end_actions": actions})
                self._assert_invalid_request(response, error_code)
                self.assertEqual(self._state_marker(), marker)


if __name__ == "__main__":
    unittest.main()
