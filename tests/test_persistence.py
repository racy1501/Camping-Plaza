"""露营广场 SQLite JSON 快照持久化测试

仅使用 Python 标准库 unittest + tempfile + sqlite3。
所有测试只在临时目录下操作数据库，不读取或写入正式 camping_plaza.db。
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest

# 将 camping_plaza 包加入路径（不依赖 __init__.py，Python 3 命名空间包）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class PersistenceTestCase(unittest.TestCase):
    """公共基类：每个测试独立临时目录与数据库路径"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.db_path = os.path.join(self._td.name, "test.db")

    def _snapshot_rows(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute(
                "SELECT id, snapshot_json FROM runtime_snapshot"
            ).fetchall()
        finally:
            conn.close()

    def _write_snapshot_json(self, raw: str):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ? WHERE id = 1",
                (raw,),
            )
            conn.commit()
        finally:
            conn.close()

    def _write_snapshot_dict(self, payload: dict):
        self._write_snapshot_json(json.dumps(payload, ensure_ascii=False))


class FreshDatabaseTests(PersistenceTestCase):
    """新数据库首次启动"""

    def test_first_start_creates_table_and_single_snapshot(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        # 自动生成新游戏
        self.assertEqual(engine.state.day, 1)
        self.assertEqual(engine.state.turn, 1)
        self.assertEqual(engine.state.balance, 1000)
        # 快照表存在且仅一行
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        # 快照 JSON 可解析且含版本号
        payload = json.loads(rows[0][1])
        self.assertEqual(payload["snapshot_version"], 1)


class FullSaveRestoreTests(PersistenceTestCase):
    """完整保存与恢复"""

    def test_full_state_roundtrip(self):
        engine = CampingPlazaEngine(db_path=self.db_path)

        # 手动构造丰富状态
        engine.state.day = 3
        engine.state.turn = 4
        engine.state.balance = 2345
        engine.state.reputation_rate = 72.5
        engine.state.total_reviews = 8
        engine.state.total_rating_sum = 33
        engine.state.today_income = {
            "accommodation": 300, "campsite": 60, "dining": 45, "entertainment": 80
        }
        engine.state.today_events = ["测试事件A", "测试事件B"]
        engine.state.decisions_left = 1
        engine.state.reservation = {
            "group_size": 3, "economic_level": 2,
            "spending_habit": 0, "temperament": 1, "tent_id": 5
        }
        engine.state.reserved_tent_id = 5
        engine.state.reserved_tent_day = 4
        engine.state.greenery_processed_today = True
        engine.state.day_to_overnight_cache = ["转过夜缓存事件"]
        engine.state.turn_settled = True

        # 帐篷内部字段
        engine.tents[2].level = 2
        engine.tents[2].status = "occupied"
        engine.tents[2].occupied_by = 99
        engine.tents[2].next_breakdown_turn = 123
        engine.tents[2].satisfaction_bonus = 6.0
        engine.tents[5].status = "reserved"

        # 设施字段
        engine.facilities["dining"].level = 2
        engine.facilities["dining"].dining_spend_probability = 0.7
        engine.facilities["greenery"].greenery_satisfaction = 7.5

        # NPC 池全部关键字段
        npc = NPCGroup(
            id=7, group_size=2, visit_type="overnight", arrival_turn=2,
            location="tent_2", total_satisfaction=85, has_left=False,
            review_left=True, review_rating=4, economic_level=2,
            spending_habit=0, temperament=1, visit_count=3,
            last_visit_day=1, is_reserved=True, paid=True
        )
        engine.npc_pool.append(npc)
        engine._npc_id_counter = 42
        engine.npc_history.append({
            "id": 1, "group_size": 2, "economic_level": 1,
            "spending_habit": 1, "temperament": 0,
            "visit_count": 1, "last_visit_day": 2
        })

        self.assertTrue(engine.save_state())

        # 同一 db_path 新建引擎恢复
        restored = CampingPlazaEngine(db_path=self.db_path)
        s = restored.state
        self.assertEqual(s.day, 3)
        self.assertEqual(s.turn, 4)
        self.assertEqual(s.balance, 2345)
        self.assertEqual(s.reputation_rate, 72.5)
        self.assertEqual(s.total_reviews, 8)
        self.assertEqual(s.total_rating_sum, 33)
        self.assertEqual(s.today_income["accommodation"], 300)
        self.assertEqual(s.today_income["entertainment"], 80)
        self.assertEqual(s.today_events, ["测试事件A", "测试事件B"])
        self.assertEqual(s.decisions_left, 1)
        self.assertEqual(s.reservation["group_size"], 3)
        self.assertEqual(s.reservation["economic_level"], 2)
        self.assertEqual(s.reservation["spending_habit"], 0)
        self.assertEqual(s.reservation["temperament"], 1)
        self.assertEqual(s.reservation["tent_id"], 5)
        self.assertEqual(s.reserved_tent_id, 5)
        self.assertEqual(s.reserved_tent_day, 4)
        self.assertTrue(s.greenery_processed_today)
        self.assertEqual(s.day_to_overnight_cache, ["转过夜缓存事件"])
        self.assertTrue(s.turn_settled)

        # 帐篷键恢复为 int
        self.assertIn(2, restored.tents)
        self.assertNotIn("2", restored.tents)
        t2 = restored.tents[2]
        self.assertEqual(t2.level, 2)
        self.assertEqual(t2.status, "occupied")
        self.assertEqual(t2.occupied_by, 99)
        self.assertEqual(t2.next_breakdown_turn, 123)
        self.assertEqual(t2.satisfaction_bonus, 6.0)
        self.assertEqual(restored.tents[5].status, "reserved")

        self.assertEqual(restored.facilities["dining"].level, 2)
        self.assertEqual(restored.facilities["dining"].dining_spend_probability, 0.7)
        self.assertEqual(restored.facilities["greenery"].greenery_satisfaction, 7.5)

        self.assertEqual(len(restored.npc_pool), 1)
        n = restored.npc_pool[0]
        self.assertEqual(n.id, 7)
        self.assertEqual(n.group_size, 2)
        self.assertEqual(n.visit_type, "overnight")
        self.assertEqual(n.arrival_turn, 2)
        self.assertEqual(n.location, "tent_2")
        self.assertEqual(n.total_satisfaction, 85)
        self.assertFalse(n.has_left)
        self.assertTrue(n.review_left)
        self.assertEqual(n.review_rating, 4)
        self.assertEqual(n.economic_level, 2)
        self.assertEqual(n.spending_habit, 0)
        self.assertEqual(n.temperament, 1)
        self.assertEqual(n.visit_count, 3)
        self.assertEqual(n.last_visit_day, 1)
        self.assertTrue(n.is_reserved)
        self.assertTrue(n.paid)

        self.assertEqual(restored._npc_id_counter, 42)
        self.assertEqual(len(restored.npc_history), 1)
        self.assertEqual(restored.npc_history[0]["id"], 1)
        self.assertEqual(restored.npc_history[0]["last_visit_day"], 2)


class OverwriteTests(PersistenceTestCase):
    """多次保存覆盖而非追加"""

    def test_repeated_saves_keep_single_row_and_latest_state(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine.state.balance = 1111
        engine.state.turn = 2
        self.assertTrue(engine.save_state())

        engine.state.balance = 2222
        engine.state.turn = 5
        self.assertTrue(engine.save_state())

        engine.state.balance = 3333
        engine.state.turn = 6
        self.assertTrue(engine.save_state())

        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.balance, 3333)
        self.assertEqual(restored.state.turn, 6)


class EmptyDatabaseFallbackTests(PersistenceTestCase):
    """数据库文件存在但没有快照时安全回退"""

    def test_empty_database_falls_back_to_new_game(self):
        # 只建表，不写快照
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE runtime_snapshot (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)
            conn.commit()
        finally:
            conn.close()

        engine = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(engine.state.day, 1)
        self.assertEqual(engine.state.balance, 1000)
        # 回退后自动写入有效快照
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][1])
        self.assertEqual(payload["snapshot_version"], 1)


class CorruptJsonFallbackTests(PersistenceTestCase):
    """损坏 JSON 安全回退"""

    def test_corrupt_snapshot_falls_back_and_gets_replaced(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(engine.save_state())

        # 手动写入非法 JSON
        self._write_snapshot_json("{not valid json !!!")

        # 新建引擎不得抛异常，回退新游戏
        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.day, 1)
        self.assertEqual(restored.state.balance, 1000)

        # 损坏内容被新的有效快照替换
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][1])
        self.assertEqual(payload["snapshot_version"], 1)


class NpcIdContinuityTests(PersistenceTestCase):
    """NPC ID 连续性"""

    def test_npc_id_counter_continues_after_restore(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        npc = NPCGroup(id=engine._next_npc_id(), group_size=2, visit_type="day")
        engine.npc_pool.append(npc)
        engine._next_npc_id()
        engine._next_npc_id()
        saved_counter = engine._npc_id_counter
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored._npc_id_counter, saved_counter)
        new_id = restored._next_npc_id()
        self.assertEqual(new_id, saved_counter + 1)
        existing_ids = {n.id for n in restored.npc_pool}
        self.assertNotIn(new_id, existing_ids)


class IsolationTests(PersistenceTestCase):
    """测试隔离：不创建或修改项目正式数据库"""

    def test_no_formal_database_created_in_project(self):
        formal_db = os.path.join(_PROJECT_ROOT, "camping_plaza", "camping_plaza.db")
        self.assertFalse(
            os.path.exists(formal_db),
            "测试不得在项目目录创建正式 camping_plaza.db"
        )


class PartialCorruptFallbackTests(PersistenceTestCase):
    """合法 JSON 但嵌套结构损坏时安全回退，不污染实例状态"""

    def test_nested_tent_corruption_falls_back_to_clean_new_game(self):
        # 先生成一份有效快照
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        self.assertEqual(len(original_rows), 1)
        valid_payload = json.loads(original_rows[0][1])

        # 构造顶层字段齐全、但帐篷数据无法构造 Tent 的损坏快照
        valid_payload["state"]["day"] = 99
        valid_payload["state"]["turn"] = 4
        valid_payload["state"]["balance"] = 777
        valid_payload["tents"]["2"] = {"missing_id": True}  # 缺少 id/capacity
        self._write_snapshot_dict(valid_payload)

        # 同一 db_path 新建引擎不得抛异常，应完整回退新游戏
        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.day, 1)
        self.assertEqual(restored.state.turn, 1)
        self.assertEqual(restored.state.balance, 1000)
        self.assertEqual(len(restored.tents), 6)
        self.assertEqual(len(restored.facilities), 3)

        # 损坏快照已被新的有效快照替换
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        new_payload = json.loads(rows[0][1])
        self.assertEqual(new_payload["snapshot_version"], 1)
        self.assertEqual(new_payload["state"]["day"], 1)
        self.assertNotEqual(new_payload["state"]["day"], 99)


class SnapshotVersionMismatchTests(PersistenceTestCase):
    """快照版本号与当前版本不一致时完整回退新游戏"""

    def test_old_snapshot_version_falls_back_cleanly(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        valid_payload["snapshot_version"] = 999
        valid_payload["state"]["day"] = 88
        valid_payload["state"]["balance"] = 12345
        self._write_snapshot_dict(valid_payload)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.day, 1)
        self.assertEqual(restored.state.turn, 1)
        self.assertEqual(restored.state.balance, 1000)
        self.assertEqual(len(restored.tents), 6)

        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        new_payload = json.loads(rows[0][1])
        self.assertEqual(new_payload["snapshot_version"], CampingPlazaEngine.SNAPSHOT_VERSION)
        self.assertEqual(new_payload["state"]["day"], 1)


if __name__ == "__main__":
    unittest.main()
