"""露营广场后端长程压力测试与持久化集成测试

- 多日合法经营模拟（多固定随机种子，每个存档 ≥15 游戏日）
- 经营过程中定期重启恢复并全量比对状态
- 每步检查状态一致性不变量
- 确定性组合场景（多顶故障、优先级、预定/清洁/转过夜缓存、重启不重复收费等）

仅使用 Python 标准库 unittest/tempfile/random/unittest.mock；
不启动 FastAPI 服务，直接调用 game_api 中函数；
不触碰正式 camping_plaza.db；结果可重复。
"""

import json
import os
import random
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import asdict
from unittest import mock

# 将 camping_plaza 包加入路径（不依赖 __init__.py，Python 3 命名空间包）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine, NPCGroup

EXPECTED_CAPACITY = {1: 2, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}
INCOME_KEYS = ("accommodation", "campsite", "dining", "entertainment")


def snapshot_dict(eng):
    """提取引擎完整可比对状态（与快照内容一致的结构）"""
    return {
        "state": asdict(eng.state),
        "tents": {tid: asdict(t) for tid, t in eng.tents.items()},
        "facilities": {name: asdict(f) for name, f in eng.facilities.items()},
        "npc_pool": [asdict(n) for n in eng.npc_pool],
        "npc_history": [dict(h) for h in eng.npc_history],
        "counter": eng._npc_id_counter,
    }


class LongRunTestCase(unittest.TestCase):
    """公共基类：独立临时数据库 + 替换 game_api.engine"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(
            dir=os.path.join(
                os.environ.get("TEMP")
                or os.environ.get("TMP")
                or tempfile.gettempdir(),
                "camping_plaza_fix_temp",
            )
        )
        self.addCleanup(self._td.cleanup)
        self.db_path = os.path.join(self._td.name, "test.db")
        self.engine = CampingPlazaEngine(db_path=self.db_path)
        self._original_engine = game_api.engine
        game_api.engine = self.engine
        self.expected_unlocked_tent_ids = {1}

    def tearDown(self):
        game_api.engine = self._original_engine

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _action(self, action, params=None):
        return game_api.do_action(game_api.ActionRequest(action=action, params=params))

    def _snapshot_rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT id, snapshot_json, updated_at FROM runtime_snapshot"
            ).fetchall()
        finally:
            conn.close()

    def _restart(self):
        """模拟服务重启：保存后新建引擎并替换 game_api.engine，全量比对"""
        before = snapshot_dict(self.engine)
        self.assertTrue(self.engine.save_state())
        restored = CampingPlazaEngine(db_path=self.db_path)
        after = snapshot_dict(restored)
        self.assertEqual(before, after, "重启恢复后状态与保存前不一致")
        self.engine = restored
        game_api.engine = self.engine
        return restored

    def _unlock_tents(self, *tent_ids):
        """场景准备：仅为当前测试显式开放所需帐篷，不改变默认开局规则。"""
        for tent_id in tent_ids:
            self.engine.tents[tent_id].is_unlocked = True
            self.expected_unlocked_tent_ids.add(tent_id)

    def _check_invariants(self, step_tag=""):
        eng = self.engine
        s = eng.state
        tag = f"[{step_tag}] " if step_tag else ""
        unlocked_ids = {tid for tid, tent in eng.tents.items() if tent.is_unlocked}
        self.assertEqual(unlocked_ids, self.expected_unlocked_tent_ids,
                         f"{tag}帐篷解锁集合异常")

        # 全局字段
        self.assertGreaterEqual(s.day, 1, f"{tag}day 非法")
        self.assertIn(s.turn, (1, 2, 3, 4, 5, 6), f"{tag}turn 越界: {s.turn}")
        self.assertGreaterEqual(s.decisions_left, 0, f"{tag}decisions_left 为负")

        # 帐篷
        self.assertEqual(set(eng.tents.keys()), {1, 2, 3, 4, 5, 6}, f"{tag}帐篷ID异常")
        for tid, tent in eng.tents.items():
            self.assertEqual(tent.id, tid, f"{tag}帐篷{tid} id 不一致")
            self.assertEqual(tent.capacity, EXPECTED_CAPACITY[tid],
                             f"{tag}帐篷{tid}容量丢失")
            self.assertIn(tent.status,
                          ("available", "occupied", "cleaning", "broken", "reserved"),
                          f"{tag}帐篷{tid}非法状态 {tent.status}")
            if not tent.is_unlocked:
                self.assertEqual(tent.status, "available",
                                 f"{tag}锁定帐篷{tid}不应进入运营状态")
                self.assertIsNone(tent.occupied_by,
                                  f"{tag}锁定帐篷{tid}不应携带住客")
                self.assertEqual(tent.next_breakdown_turn, 0,
                                 f"{tag}锁定帐篷{tid}不应持有故障计划")
            if tent.status == "occupied":
                self.assertIsNotNone(tent.occupied_by, f"{tag}帐篷{tid}occupied却无住客")
            # broken 帐篷允许保留 occupied_by（故障不赶客，修好后恢复 occupied）
            if tent.status in ("available", "cleaning", "reserved"):
                self.assertIsNone(tent.occupied_by,
                                  f"{tag}帐篷{tid}{tent.status}却携带住客标记")

        # NPC 与帐篷互相引用一致
        active = [n for n in eng.npc_pool if not n.has_left]
        npc_ids = [n.id for n in eng.npc_pool]
        self.assertEqual(len(npc_ids), len(set(npc_ids)), f"{tag}NPC ID 重复")
        active_by_id = {n.id: n for n in active}

        for tid, tent in eng.tents.items():
            # occupied 必须有住客；broken 允许保留住客（故障不赶客）
            if tent.status in ("occupied", "broken") and tent.occupied_by is not None:
                guest = active_by_id.get(tent.occupied_by)
                self.assertIsNotNone(guest,
                                     f"{tag}帐篷{tid}住客 {tent.occupied_by} 不在场")
                # 住客保留帐篷占用权（occupied_by），当前位置可以是本帐篷或消费点
                # （dining/entertainment/hot_spring）；gate/campsite/leaving/其他帐篷/非法位置均不允许
                self.assertIn(
                    guest.location,
                    (f"tent_{tid}", "dining", "entertainment", "hot_spring"),
                    f"{tag}帐篷{tid}住客位置非法: {guest.location}",
                )

        for npc in active:
            if npc.location.startswith("tent_"):
                tid = int(npc.location.split("_")[1])
                self.assertIn(tid, eng.tents, f"{tag}NPC{npc.id}在不存在的帐篷")
                self.assertEqual(eng.tents[tid].occupied_by, npc.id,
                                 f"{tag}NPC{npc.id}位置与帐篷{tid}占用不一致")
                # 住客所在帐篷必须是 occupied 或 broken（故障时住客留在帐篷内）
                self.assertIn(eng.tents[tid].status, ("occupied", "broken"),
                              f"{tag}NPC{npc.id}所在帐篷{tid}状态异常")

        # ID 计数器不小于任何已生成 NPC ID（含历史）
        all_ids = npc_ids + [h["id"] for h in eng.npc_history]
        if all_ids:
            self.assertGreaterEqual(eng._npc_id_counter, max(all_ids),
                                    f"{tag}_npc_id_counter 小于已生成ID")

        # 收入结构
        for key in INCOME_KEYS:
            self.assertIn(key, s.today_income, f"{tag}today_income 缺少 {key}")

        # 快照单行
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1, f"{tag}runtime_snapshot 行数异常")
        self.assertEqual(rows[0][0], 1, f"{tag}runtime_snapshot id 异常")


class MultiDayOperationTests(LongRunTestCase):
    """多日合法经营模拟 + 定期重启恢复 + 不变量检查"""

    SEEDS = [11, 23, 37, 42, 58, 67, 79, 83, 91, 97]
    TARGET_DAYS = 15
    RESTART_INTERVAL = 25
    MAX_STEPS = 8000

    def _choose_and_execute(self, rng, stall_guard):
        """从当前可用操作中选择并执行一步，返回是否推进了回合/天数"""
        actions = game_api.mcp_available_actions()["available_actions"]
        by_name = {}
        for a in actions:
            by_name.setdefault(a["action"], []).append(a)

        day_turn_before = (self.engine.state.day, self.engine.state.turn)

        # 1. broken 帐篷优先逐个维修
        if "repair_tent" in by_name:
            a = by_name["repair_tent"][0]
            self._action("repair_tent", a["params"])
            return False

        # 2. 批量清洁
        if "clean_tents" in by_name and rng.random() < 0.9:
            self._action("clean_tents", by_name["clean_tents"][0]["params"])
            return False

        # 4. 推进类操作（防停滞时强制）
        advance_like = []
        for name in ("advance_turn", "new_day"):
            if name in by_name:
                advance_like.append(name)
        if stall_guard[0] >= 20 and advance_like:
            self._action(advance_like[0])
            return True

        # 5. 其余经营操作随机（提升服务/升级/绿化）
        optional = []
        for name in ("improve_service", "upgrade_facility",
                     "manage_greenery"):
            if name in by_name:
                optional.append(by_name[name][0])
        if optional and rng.random() < 0.5:
            a = rng.choice(optional)
            self._action(a["action"], a.get("params"))
            return False

        # 6. 默认推进（营业回合 mcp actions 不列 advance_turn，直接调用接口）
        if self.engine.state.turn <= 5:
            game_api.advance_turn()
        else:
            self._action("new_day")
        return (self.engine.state.day, self.engine.state.turn) != day_turn_before

    def test_multi_day_operation_with_restarts(self):
        total_restarts = 0
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                # 每个种子独立存档
                self.db_path = os.path.join(self._td.name, f"seed_{seed}.db")
                self.engine = CampingPlazaEngine(db_path=self.db_path)
                game_api.engine = self.engine

                rng = random.Random(seed)
                stall_guard = [0]
                restarts = 0
                steps = 0

                while self.engine.state.day <= self.TARGET_DAYS:
                    steps += 1
                    self.assertLess(steps, self.MAX_STEPS,
                                    f"seed={seed} 步数超限，疑似死锁")

                    progressed = self._choose_and_execute(rng, stall_guard)
                    stall_guard[0] = 0 if progressed else stall_guard[0] + 1

                    self._check_invariants(f"seed={seed} step={steps}")

                    if steps % self.RESTART_INTERVAL == 0:
                        self._restart()
                        restarts += 1

                # 存档目标天数达成且状态仍可核对
                self.assertGreaterEqual(self.engine.state.day, self.TARGET_DAYS)
                total_restarts += restarts
                self.assertGreater(restarts, 0, f"seed={seed} 未发生重启恢复")


class DeterministicScenarioTests(LongRunTestCase):
    """确定性组合场景"""

    def _disable_breakdowns(self):
        for t in self.engine.tents.values():
            t.next_breakdown_turn = 999999 if t.is_unlocked else 0

    def test_multiple_broken_same_turn_repair_and_advance(self):
        """同一回合多顶 broken，通过 Turn Plan 提交维修后正常推进"""
        self._disable_breakdowns()
        self._unlock_tents(3)
        self.engine.state.turn = 2
        self.engine.tents[1].status = "broken"
        self.engine.tents[3].status = "broken"
        self.engine.state.decisions_left = 3
        # 预置当天到达计划为空，避免营业推进时生成新客入住刚修好的帐篷
        self.engine.state.today_arrival_plan_day = 1
        self.engine.state.today_arrival_plan = []

        # 通过 Turn Plan 提交两顶维修，推进执行（mock 随机抑制客流，确保帐篷不被入住）
        plan_result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[],
            actions=[
                game_api.ActionRequest(action="repair_tent", params={"tent_id": 1}),
                game_api.ActionRequest(action="repair_tent", params={"tent_id": 3}),
            ],
        ))
        self.assertTrue(plan_result["success"])

        with mock.patch("game_engine.random.random", return_value=0.99):
            result = game_api.advance_turn()
        self.assertEqual(result["turn"], 3)
        self.assertEqual(self.engine.tents[1].status, "available")
        self.assertEqual(self.engine.tents[3].status, "available")
        self._check_invariants("multi-broken")

    def test_plan_submitted_broken_cleaning(self):
        """已提交计划时只返回 advance_turn；broken 不再封锁清洁"""
        self._disable_breakdowns()
        self._unlock_tents(2, 4)
        self.engine.state.turn = 3
        self.engine.tents[2].status = "broken"
        self.engine.tents[4].status = "cleaning"
        self.engine.state.decisions_left = 3
        self.engine.state.today_conflict_event = {"status": "no_event"}
        # 预置当天到达计划为空，避免营业推进时生成新客入住帐篷并消费（干扰住客位置不变量）
        self.engine.state.today_arrival_plan_day = 1
        self.engine.state.today_arrival_plan = []

        # 未提交计划时 advance 不推进、不重复结算，cleaning 状态保留
        income_before = dict(self.engine.state.today_income)
        result = game_api.advance_turn()
        self.assertEqual(result["turn"], 3)
        self.assertEqual(dict(self.engine.state.today_income), income_before)
        self.assertEqual(self.engine.tents[4].status, "cleaning")

        # 提交空计划推进：cleaning 状态跨回合保留（mock 随机抑制客流）
        plan_result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[], actions=[],
        ))
        self.assertTrue(plan_result["success"])
        actions = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual([a["action"] for a in actions], ["advance_turn"])
        with mock.patch("game_engine.random.random", return_value=0.99):
            result = game_api.advance_turn()
        self.assertEqual(result["turn"], 4)
        self.assertEqual(self.engine.tents[4].status, "cleaning")

        # 新回合可完成清洁
        result = self.engine.clean_tents()
        self.assertTrue(result["success"])
        self.assertEqual(self.engine.tents[4].status, "available")
        self._check_invariants("priority")

    def test_reserved_tent_cleaning_then_reserved_then_checkin(self):
        """今日预定帐篷退房→cleaning→批量清洁→reserved→预定客入住"""
        self._disable_breakdowns()
        eng = self.engine
        self._unlock_tents(3)
        eng.state.day = 2
        eng.state.turn = 1
        reserved_npc_id = eng._next_npc_id()
        eng.state.reservations = [{
            "npc_id": reserved_npc_id,
            "group_size": 1,
            "visit_type": "overnight",
            "arrival_day": 2,
            "paid": True,
            "status": "accepted",
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "tent_id": 3,
        }]
        with mock.patch.object(eng, "_roll_arrival_turn", return_value=2):
            self.assertTrue(eng._ensure_today_arrival_plan())

        # 帐篷 3 昨晚被普通过夜客住着
        guest = NPCGroup(id=eng._next_npc_id(), group_size=1,
                         visit_type="overnight", location="tent_3")
        eng.npc_pool.append(guest)
        eng.tents[3].status = "occupied"
        eng.tents[3].occupied_by = guest.id

        # Turn 1：强制退房（random<0.5 退），帐篷进入 cleaning
        with mock.patch("game_engine.random.random", return_value=0.1):
            game_api.advance_turn()
        self.assertEqual(eng.tents[3].status, "cleaning")
        self.assertIsNone(eng.tents[3].occupied_by)

        # Turn 2：通过 Turn Plan 提交清洁（free action），执行后预定帐篷恢复 reserved 并入住预定客
        plan_result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[
                game_api.ActionRequest(action="clean_tents", params={"tent_ids": [3]}),
            ],
            actions=[],
        ))
        self.assertTrue(plan_result["success"])
        self.assertEqual(eng.tents[3].status, "occupied")
        reserved_npcs = [n for n in eng.npc_pool
                         if n.is_reserved and n.location == "tent_3"]
        self.assertEqual(len(reserved_npcs), 1)
        self.assertTrue(reserved_npcs[0].paid)
        self.assertEqual(eng.tents[3].occupied_by, reserved_npcs[0].id)
        # 预定状态已结清
        self.assertEqual(eng.state.reservations, [])
        reservation_entries = [
            entry for entry in eng.state.today_arrival_plan
            if entry.get("source") == "reservation"
            and entry.get("npc_id") == reserved_npc_id
        ]
        self.assertEqual(len(reservation_entries), 1)
        self.assertEqual(reservation_entries[0]["arrival_status"], "arrived")
        self._check_invariants("reserved-flow")

    def test_day_to_overnight_conversion_survives_restart(self):
        """Turn 4 日转夜结算后，转夜客状态跨重启保留；后续推进正常"""
        self._disable_breakdowns()
        eng = self.engine
        eng.state.day = 1
        eng.state.turn = 4
        guest = NPCGroup(id=eng._next_npc_id(), group_size=1,
                         visit_type="day", location="dining",
                         total_satisfaction=90)
        eng.npc_pool.append(guest)
        eng.state.today_arrival_plan = [{
            "npc_id": guest.id, "group_size": 1, "visit_type": "day",
            "arrival_turn": 1, "planned_day": 1, "arrival_status": "arrived",
            "day_to_overnight_intent": True,
        }]
        eng.state.today_arrival_plan_day = 1

        # Turn 4：提交空计划后推进，日转夜结算
        plan_result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[], actions=[],
        ))
        self.assertTrue(plan_result["success"])
        with mock.patch("game_engine.random.random", return_value=0.99):
            game_api.advance_turn()  # Turn 4 → 5
        self.assertEqual(eng.state.turn, 5)
        self.assertEqual(guest.visit_type, "overnight")
        self.assertEqual(guest.location, "tent_1")
        self.assertEqual(eng.tents[1].occupied_by, guest.id)

        # 重启恢复：转夜客与帐篷占用保留
        self._restart()
        restored_guest = next(n for n in self.engine.npc_pool if n.id == guest.id)
        self.assertEqual(restored_guest.visit_type, "overnight")
        self.assertEqual(restored_guest.location, "tent_1")
        self.assertEqual(self.engine.tents[1].occupied_by, guest.id)

        # Turn 5：提交空计划后推进到日终管理，转夜客仍正常在册
        plan_result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[], actions=[],
        ))
        self.assertTrue(plan_result["success"])
        with mock.patch("game_engine.random.random", return_value=0.99):
            game_api.advance_turn()  # Turn 5 → 6
        self.assertEqual(self.engine.state.turn, 6)
        self.assertFalse(restored_guest.has_left)
        self.assertEqual(self.engine.tents[1].occupied_by, guest.id)
        self._check_invariants("d2o-restart")

    def test_clean_then_restart_not_back_to_cleaning(self):
        """批量清洁后立刻重启，状态不得回到 cleaning"""
        self._disable_breakdowns()
        self._unlock_tents(2)
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[2].status = "cleaning"
        # 预置当天到达计划为空，避免营业推进时生成新客入住待清洁帐篷
        self.engine.state.today_arrival_plan_day = 1
        self.engine.state.today_arrival_plan = []

        # Turn 1 → Turn 2：按正常流程推进到可提交计划的营业回合
        with mock.patch("game_engine.random.random", return_value=0.99):
            game_api.advance_turn()
        self.assertEqual(self.engine.state.turn, 2)

        # 通过 Turn Plan 提交清洁（free action，不消耗决策点）
        plan_result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[
                game_api.ActionRequest(action="clean_tents", params={"tent_ids": [1, 2]}),
            ],
            actions=[],
        ))
        self.assertTrue(plan_result["success"])

        # advance 执行计划中的清洁
        with mock.patch("game_engine.random.random", return_value=0.99):
            game_api.advance_turn()

        self._restart()
        self.assertEqual(self.engine.tents[1].status, "available")
        self.assertEqual(self.engine.tents[2].status, "available")

    def test_repeated_saves_single_row(self):
        """连续多次保存始终只有一行快照"""
        for i in range(5):
            self.engine.state.balance = 1000 + i * 100
            self.assertTrue(self.engine.save_state())
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][1])
        self.assertEqual(payload["state"]["balance"], 1400)

    def test_read_only_endpoints_do_not_touch_snapshot(self):
        """所有纯读取接口不得改变快照内容或更新时间"""
        self.assertTrue(self.engine.save_state())
        rows_before = self._snapshot_rows()

        game_api.get_state()
        game_api.get_display_state()
        game_api.get_map_data()
        game_api.mcp_state()
        game_api.mcp_available_actions()

        rows_after = self._snapshot_rows()
        self.assertEqual(rows_before, rows_after)

    def test_check_invariants_accepts_consuming_occupant_locations(self):
        """住客在消费点（dining）时仍保留帐篷占用权，不变量应通过"""
        self._disable_breakdowns()
        eng = self.engine
        eng.state.today_arrival_plan_day = 1
        eng.state.today_arrival_plan = []
        npc = NPCGroup(id=eng._next_npc_id(), group_size=1,
                       visit_type="overnight", location="dining",
                       total_satisfaction=70)
        eng.npc_pool.append(npc)
        eng.tents[1].status = "occupied"
        eng.tents[1].occupied_by = npc.id
        eng.save_state()

        # 消费点 location 不破坏帐篷占用不变量
        self._check_invariants("dining-occupant")

    def test_check_invariants_rejects_illegal_occupant_locations(self):
        """住客位置为其他帐篷或 gate 时，不变量应失败"""
        self._disable_breakdowns()
        eng = self.engine
        eng.state.today_arrival_plan_day = 1
        eng.state.today_arrival_plan = []
        npc = NPCGroup(id=eng._next_npc_id(), group_size=1,
                       visit_type="overnight", location="tent_2",
                       total_satisfaction=70)
        eng.npc_pool.append(npc)
        eng.tents[1].status = "occupied"
        eng.tents[1].occupied_by = npc.id
        eng.save_state()

        # 其他帐篷位置非法
        with self.assertRaises(AssertionError):
            self._check_invariants("bad-other-tent")

        # gate 位置非法
        npc.location = "gate"
        with self.assertRaises(AssertionError):
            self._check_invariants("bad-gate")


if __name__ == "__main__":
    unittest.main()
