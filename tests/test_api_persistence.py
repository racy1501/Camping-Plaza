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

    def test_accept_reservation_success_saves(self):
        self.engine.state.turn = 2
        self.engine.state.decisions_left = 3
        self.engine.state.reservation = {
            "group_size": 1,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("accept_reservation", {"group_size": 1})
            save_mock.assert_called_once()

    def test_reject_reservation_saves(self):
        self.engine.state.turn = 2
        self.engine.state.reservation = {
            "group_size": 2,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("reject_reservation")
            save_mock.assert_called_once()

    def test_upgrade_tent_saves(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        with mock.patch.object(self.engine, "save_state") as save_mock:
            self._action("upgrade_tent", {"tent_id": 1})
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

    def test_accept_reservation_capacity_fail_with_complaint_saves(self):
        """容量不足失败但命中抱怨事件后仍保存"""
        self.engine.state.turn = 2
        self.engine.state.decisions_left = 3
        self.engine.state.reservation = {
            "group_size": 6,  # 超过所有帐篷最大容量 5
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        with mock.patch.object(self.engine, "save_state") as save_mock:
            with mock.patch("game_engine.random.random", return_value=0.1):
                result = self._action("accept_reservation", {"group_size": 6})
                self.assertFalse(result["success"])
                save_mock.assert_called_once()


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

    def test_capacity_fail_complaint_recovery(self):
        """容量不足抱怨事件保存后恢复，reservation 与 today_events 保留"""
        self.engine.state.turn = 2
        self.engine.state.reservation = {
            "group_size": 6,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        initial_balance = self.engine.state.balance

        with mock.patch("game_engine.random.random", return_value=0.1):
            result = self._action("accept_reservation", {"group_size": 6})
        self.assertFalse(result["success"])

        restored = self._new_engine_from_db()
        self.assertIsNotNone(restored.state.reservation)
        self.assertEqual(restored.state.reservation["group_size"], 6)
        self.assertIn("不太满意的帖子", "".join(restored.state.today_events))
        self.assertEqual(restored.state.balance, initial_balance)
        self.assertEqual(restored.state.today_income["accommodation"], 0)

    def test_upgrade_tent_recovery(self):
        """升级帐篷后恢复，等级和余额变化仍存在"""
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        initial_level = self.engine.tents[1].level
        initial_balance = self.engine.state.balance

        self._action("upgrade_tent", {"tent_id": 1})

        restored = self._new_engine_from_db()
        self.assertEqual(restored.tents[1].level, initial_level + 1)
        self.assertLess(restored.state.balance, initial_balance)

    def test_upgrade_facility_recovery(self):
        """升级设施后恢复，等级和余额变化仍存在"""
        self.engine.state.turn = 6
        self.engine.state.balance = 99999
        initial_level = self.engine.facilities["dining"].level
        initial_balance = self.engine.state.balance

        self._action("upgrade_facility", {"facility_name": "dining"})

        restored = self._new_engine_from_db()
        self.assertEqual(restored.facilities["dining"].level, initial_level + 1)
        self.assertLess(restored.state.balance, initial_balance)


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

    def test_turn6_immediate_management_actions_still_work(self):
        self.engine.state.turn = 6
        self.engine.tents[1].status = "broken"
        self.engine.state.decisions_left = 3

        result = self._action("repair_tent", {"tent_id": 1})

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.tents[1].status, "available")

    def test_reservation_actions_still_work(self):
        self.engine.state.turn = 2
        self.engine.state.reservation = {
            "group_size": 1,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }

        accept_result = self._action("accept_reservation", {"group_size": 1})
        self.assertTrue(accept_result["success"])
        self.assertIsNotNone(self.engine.state.reserved_tent_id)

        self.engine.state.reservation = {
            "group_size": 1,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
        }
        reject_result = self._action("reject_reservation")
        self.assertTrue(reject_result["success"])
        self.assertIsNone(self.engine.state.reservation)


class McpTurnPlanTests(ApiPersistenceTestCase):
    def test_mcp_state_exposes_food_stock(self):
        self.engine.state.food_stock = 9

        state = game_api.mcp_state()

        self.assertEqual(state["food_stock"], 9)

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

if __name__ == "__main__":
    unittest.main()
