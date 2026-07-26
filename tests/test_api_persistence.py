"""露营广场 API 持久化回归测试

验证 game_api.py 的写入口在执行后都会触发 engine.save_state()，
并且关键副作用可通过同一数据库恢复。

仅使用 Python 标准库 unittest/tempfile/unittest.mock；
不启动 FastAPI 服务，直接调用 game_api 中函数；
所有测试使用临时目录数据库，不触碰正式 camping_plaza.db。
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

# 将 camping_plaza 包加入路径（不依赖 __init__.py，Python 3 命名空间包）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine


class ApiPersistenceTestCase(unittest.TestCase):
    """公共基类：每个测试独立临时数据库与 game_api.engine"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = os.path.join(self._td.name, "test.db")

        # 为当前测试创建独立引擎，并替换 game_api 全局 engine
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self._original_engine = game_api.engine
        game_api.engine = self.engine

        # 屏蔽随机故障干扰，避免非预期 broken 破坏测试状态
        for tent in self.engine.tents.values():
            tent.next_breakdown_turn = 999999

    def tearDown(self):
        game_api.engine = self._original_engine

    def _new_engine_from_db(self):
        """用同一数据库路径新建引擎，模拟服务重启后恢复"""
        return CampingPlazaEngine(db_path=self.db_path)

    def _action(self, action, params=None):
        """构造 ActionRequest 并调用 game_api.do_action"""
        return game_api.do_action(game_api.ActionRequest(action=action, params=params))


class SaveStateCalledTests(ApiPersistenceTestCase):
    """验证写入口执行后都会调用 engine.save_state()"""

    def test_advance_turn_saves(self):
        with mock.patch.object(self.engine, "save_state") as save_mock:
            game_api.advance_turn()
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

    def test_broken_block_recovery_preserves_decisions(self):
        """故障阻塞 advance_turn 保存后，恢复时 decisions_left 已补足"""
        self.engine.tents[1].status = "broken"
        self.engine.tents[2].status = "broken"
        self.engine.state.decisions_left = 0
        self.engine.state.turn = 2

        game_api.advance_turn()

        restored = self._new_engine_from_db()
        self.assertEqual(restored.state.turn, 2)
        self.assertEqual(restored.state.decisions_left, 2)
        self.assertEqual(restored.tents[1].status, "broken")
        self.assertEqual(restored.tents[2].status, "broken")

    def test_clean_tents_recovery(self):
        """清洁帐篷后恢复，状态保持为 available"""
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


if __name__ == "__main__":
    unittest.main()
