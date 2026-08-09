"""人类网页动作目录 /api/actions 隔离测试

验证 game_api.get_human_actions() 将引擎状态正确整理为
适合网页读取的动作目录，且不修改存档/运行态。

所有测试使用临时目录数据库，不触碰正式 camping_plaza.db。
"""

import os
import sys
import tempfile
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine


class HumanActionsApiTestCase(unittest.TestCase):
    """公共基类：每个测试独立临时目录数据库与 game_api.engine"""

    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(
            prefix="test_human_actions_",
            ignore_cleanup_errors=True,
        )
        self.addCleanup(self._temp_dir.cleanup)
        self.db_path = os.path.join(self._temp_dir.name, "test.db")

        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self._original_engine = game_api.engine
        game_api.engine = self.engine

        # 屏蔽随机故障，避免非预期 broken 破坏断言
        for tent in self.engine.tents.values():
            tent.next_breakdown_turn = 999999

    def tearDown(self):
        game_api.engine = self._original_engine

    def _actions(self):
        """调用 GET /api/actions 对应函数"""
        return game_api.get_human_actions()

    def _snapshot_state(self):
        """抓取当前引擎关键状态快照"""
        return {
            "day": self.engine.state.day,
            "turn": self.engine.state.turn,
            "balance": self.engine.state.balance,
            "decisions_left": self.engine.state.decisions_left,
            "pending_turn_plan": self.engine.state.pending_turn_plan,
            "day_end_completed": self.engine.state.day_end_completed,
            "tent_status": {
                tid: t.status for tid, t in self.engine.tents.items()
            },
        }


class Turn1Tests(HumanActionsApiTestCase):
    def test_turn1_opening_mode(self):
        actions = self._actions()
        self.assertTrue(actions["success"])
        self.assertEqual(actions["day"], 1)
        self.assertEqual(actions["turn"], 1)
        self.assertEqual(actions["mode"], "opening")
        self.assertEqual(actions["panel_title"], "迎客准备")
        self.assertFalse(actions["planning_available"])
        self.assertFalse(actions["plan_submitted"])
        self.assertEqual(actions["free_action_candidates"], [])
        self.assertEqual(actions["decision_action_candidates"], [])

        primary = actions["primary_action"]
        self.assertEqual(primary["action"], "advance_turn")
        self.assertEqual(primary["label"], "开始营业")
        self.assertTrue(primary["enabled"])

    def test_turn1_does_not_modify_state(self):
        before = self._snapshot_state()
        self._actions()
        after = self._snapshot_state()
        self.assertEqual(before, after)


class Turn2PlanningTests(HumanActionsApiTestCase):
    def setUp(self):
        super().setUp()
        self.engine.state.turn = 2

    def test_turn2_planning_mode(self):
        actions = self._actions()
        self.assertEqual(actions["mode"], "planning")
        self.assertEqual(actions["panel_title"], "营业经营")
        self.assertTrue(actions["planning_available"])
        self.assertFalse(actions["plan_submitted"])
        self.assertEqual(actions["max_decision_actions"], 3)

        primary = actions["primary_action"]
        self.assertEqual(primary["action"], "submit_turn_plan")
        self.assertEqual(primary["label"], "提交本轮计划")
        self.assertTrue(primary["enabled"])

    def test_food_packages_from_engine_catalog(self):
        actions = self._actions()
        food_candidates = [
            c for c in actions["decision_action_candidates"]
            if c["action"] == "buy_food_package"
        ]
        self.assertEqual(len(food_candidates), 3)
        expected_keys = {"small", "medium", "large"}
        found_keys = {c["params"]["package_key"] for c in food_candidates}
        self.assertEqual(found_keys, expected_keys)

        for c in food_candidates:
            self.assertEqual(c["kind"], "decision")
            self.assertEqual(c["category"], "food")
            self.assertTrue(c["repeatable"])
            self.assertEqual(c["max_quantity"], 3)
            package = CampingPlazaEngine.FOOD_PACKAGES[c["params"]["package_key"]]
            self.assertEqual(c["price"], package["price"])
            self.assertEqual(c["portions"], package["portions"])
            self.assertIn(package["name"], c["label"])

    def test_does_not_expose_improve_service(self):
        actions = self._actions()
        actions_exposed = {c["action"] for c in actions["decision_action_candidates"]}
        self.assertNotIn("improve_service", actions_exposed)

    def test_cleaning_tent_free_candidate(self):
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[2].status = "cleaning"
        actions = self._actions()

        self.assertEqual(len(actions["free_action_candidates"]), 1)
        clean = actions["free_action_candidates"][0]
        self.assertEqual(clean["action"], "clean_tents")
        self.assertEqual(clean["kind"], "free")
        self.assertEqual(set(clean["params"]["tent_ids"]), {1, 2})
        self.assertTrue(clean["enabled"])

    def test_no_cleaning_tent_disabled(self):
        actions = self._actions()
        self.assertEqual(len(actions["free_action_candidates"]), 1)
        clean = actions["free_action_candidates"][0]
        self.assertEqual(clean["action"], "clean_tents")
        self.assertFalse(clean["enabled"])
        self.assertEqual(clean["reason"], "暂无待清洁帐篷")

    def test_multiple_broken_tents_each_a_candidate(self):
        self.engine.tents[1].status = "broken"
        self.engine.tents[2].is_unlocked = True
        self.engine.tents[2].status = "broken"
        self.engine.tents[3].is_unlocked = True
        self.engine.tents[3].status = "broken"
        actions = self._actions()

        repair_candidates = [
            c for c in actions["decision_action_candidates"]
            if c["action"] == "repair_tent"
        ]
        self.assertEqual(len(repair_candidates), 3)
        found_ids = {c["params"]["tent_id"] for c in repair_candidates}
        self.assertEqual(found_ids, {1, 2, 3})
        for c in repair_candidates:
            self.assertEqual(c["price"], CampingPlazaEngine.REPAIR_COST)
            self.assertEqual(c["kind"], "decision")
            self.assertEqual(c["category"], "repair")
            self.assertFalse(c["repeatable"])

    def test_insufficient_balance_disables_candidates(self):
        self.engine.state.balance = 50
        self.engine.tents[1].status = "broken"
        actions = self._actions()

        repair = next(
            c for c in actions["decision_action_candidates"]
            if c["action"] == "repair_tent"
        )
        self.assertFalse(repair["enabled"])
        self.assertEqual(repair["reason"], "金币不足")

        food = next(
            c for c in actions["decision_action_candidates"]
            if c["action"] == "buy_food_package" and c["price"] > 50
        )
        self.assertFalse(food["enabled"])
        self.assertEqual(food["reason"], "金币不足")

    def test_get_does_not_modify_state(self):
        self.engine.tents[1].status = "broken"
        self.engine.tents[2].status = "cleaning"
        before = self._snapshot_state()
        self._actions()
        after = self._snapshot_state()
        self.assertEqual(before, after)


class PlanSubmittedTests(HumanActionsApiTestCase):
    def setUp(self):
        super().setUp()
        self.engine.state.turn = 2

    def test_empty_plan_submitted_ready_to_advance(self):
        game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        actions = self._actions()
        self.assertEqual(actions["mode"], "ready_to_advance")
        self.assertEqual(actions["panel_title"], "营业经营")
        self.assertFalse(actions["planning_available"])
        self.assertTrue(actions["plan_submitted"])
        self.assertIsNotNone(actions["turn_plan"])

        primary = actions["primary_action"]
        self.assertEqual(primary["action"], "advance_turn")
        self.assertEqual(primary["label"], "推进经营轮次")
        self.assertTrue(primary["enabled"])

    def test_non_empty_plan_submitted_returns_summary(self):
        game_api.submit_turn_plan(
            game_api.TurnPlanRequest(
                free_actions=[game_api.ActionRequest(
                    action="clean_tents", params={"tent_ids": [1]}
                )],
                actions=[game_api.ActionRequest(
                    action="buy_food_package", params={"package_key": "small"}
                )],
            )
        )
        actions = self._actions()
        self.assertEqual(actions["mode"], "ready_to_advance")
        plan = actions["turn_plan"]
        self.assertEqual(plan["target_day"], self.engine.state.day)
        self.assertEqual(plan["target_turn"], self.engine.state.turn)
        self.assertEqual(len(plan["free_actions"]), 1)
        self.assertEqual(len(plan["decision_actions"]), 1)

    def test_submitted_plan_no_candidates(self):
        game_api.submit_turn_plan(
            game_api.TurnPlanRequest(free_actions=[], actions=[])
        )
        actions = self._actions()
        self.assertEqual(actions["free_action_candidates"], [])
        self.assertEqual(actions["decision_action_candidates"], [])


class Turn6Tests(HumanActionsApiTestCase):
    def setUp(self):
        super().setUp()
        self.engine.state.turn = 6

    def test_turn6_day_end_pending(self):
        actions = self._actions()
        self.assertEqual(actions["mode"], "day_end_pending")
        self.assertEqual(actions["panel_title"], "日终管理")
        self.assertIsNone(actions["primary_action"])
        self.assertEqual(actions["free_action_candidates"], [])
        self.assertEqual(actions["decision_action_candidates"], [])

    def test_turn6_day_end_completed(self):
        self.engine.state.day_end_completed = True
        actions = self._actions()
        self.assertEqual(actions["mode"], "day_end_completed")
        self.assertEqual(actions["panel_title"], "日终管理")
        self.assertIsNone(actions["primary_action"])
        self.assertEqual(actions["free_action_candidates"], [])
        self.assertEqual(actions["decision_action_candidates"], [])

    def test_turn6_no_state_change(self):
        before = self._snapshot_state()
        self._actions()
        after = self._snapshot_state()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
