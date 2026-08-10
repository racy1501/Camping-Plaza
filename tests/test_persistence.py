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

    def _table_names(self):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
            ).fetchall()
            return [row[0] for row in rows]
        finally:
            conn.close()


class FreshDatabaseTests(PersistenceTestCase):
    """新数据库首次启动"""

    def test_first_start_creates_table_and_single_snapshot(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        # 自动生成新游戏
        self.assertEqual(engine.state.day, 1)
        self.assertEqual(engine.state.turn, 1)
        self.assertEqual(engine.state.balance, 1000)
        self.assertEqual(
            engine.state.food_stock,
            CampingPlazaEngine.FOOD_PACKAGES["medium"]["portions"],
        )
        self.assertEqual(
            engine.state.today_events,
            [engine._build_opening_food_gift_event()],
        )
        # 快照表存在且仅一行
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        # 快照 JSON 可解析且含版本号
        payload = json.loads(rows[0][1])
        self.assertEqual(payload["snapshot_version"], 1)
        self.assertEqual(
            payload["state"]["food_stock"],
            CampingPlazaEngine.FOOD_PACKAGES["medium"]["portions"],
        )
        self.assertEqual(
            CampingPlazaEngine.FOOD_PACKAGES,
            {
                "small": {"name": "小包", "portions": 4, "price": 80},
                "medium": {"name": "中包", "portions": 8, "price": 150},
                "large": {"name": "大包", "portions": 14, "price": 250},
            },
        )
        medium_package = CampingPlazaEngine.FOOD_PACKAGES["medium"]
        self.assertIn(
            f'{medium_package["name"]}（{medium_package["portions"]}份）',
            engine.state.today_events[0],
        )


class FullSaveRestoreTests(PersistenceTestCase):
    """完整保存与恢复"""

    def test_full_state_roundtrip(self):
        engine = CampingPlazaEngine(db_path=self.db_path)

        # 手动构造丰富状态
        engine.state.day = 3
        engine.state.turn = 4
        engine.state.balance = 2345
        engine.state.day_start_balance = 1800
        engine.state.previous_day_summary = {
            "day": 2,
            "income_total": 700,
            "expense_total": 240,
            "net_income": 460,
            "guest_groups_served": 5,
        }
        engine.state.total_reviews = 8
        engine.state.total_rating_sum = 33
        engine.state.today_income = {
            "accommodation": 300, "campsite": 60, "dining": 45, "entertainment": 80
        }
        engine.state.today_events = ["测试事件A", "测试事件B"]
        engine.state.decisions_left = 1
        engine.state.improve_service_uses_today = 2
        engine.state.food_stock = 17
        engine.state.reservations = [{
            "npc_id": 42,
            "group_size": 3,
            "visit_type": "overnight",
            "arrival_day": 4,
            "status": "accepted",
            "tent_id": 5,
            "paid": True,
        }]
        engine.state.greenery_processed_today = True
        engine.state.day_to_overnight_cache = ["转过夜缓存事件"]
        engine.state.day_campsite_groups_served = 7
        engine.state.pending_turn_plan = {
            "target_day": 3,
            "target_turn": 4,
            "free_actions": [{"action": "clean_tents", "tent_ids": [1]}],
            "actions": [{"action": "repair_tent", "tent_id": 2}],
        }
        engine.state.pending_reviews = [{
            "created_day": 2,
            "rating": 4,
            "npc_id": 7,
            "visit_type": "overnight",
            "group_size": 2,
            "comment": "整体不错，是一次挺舒服的体验。",
        }]
        engine.state.review_history = [{
            "created_day": 1,
            "rating": 5,
            "npc_id": 6,
            "visit_type": "day",
            "group_size": 1,
            "comment": "很满意，下次还想再来。",
        }]

        # 帐篷内部字段
        engine.tents[2].status = "occupied"
        engine.tents[2].is_unlocked = False
        engine.tents[2].occupied_by = 99
        engine.tents[2].next_breakdown_turn = 123
        engine.tents[5].is_unlocked = True
        engine.tents[5].status = "reserved"

        # 设施字段
        engine.facilities["dining"].level = 2
        engine.facilities["dining"].dining_spend_probability = 0.7
        engine.facilities["greenery"].greenery_satisfaction = 7.5

        # NPC 池全部关键字段
        npc = NPCGroup(
            id=7, group_size=2, visit_type="overnight", arrival_turn=2,
            location="tent_2", total_satisfaction=85, has_left=False,
            review_left=True, review_rating=4, review_attempted=True, economic_level=2,
            spending_habit=0, temperament=1, visit_count=3,
            last_visit_day=1, is_reserved=True, paid=True
        )
        npc.last_dining_day = 3
        npc.checkout_turn = 2
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
        self.assertEqual(s.day_start_balance, 1800)
        self.assertEqual(s.previous_day_summary, {
            "day": 2,
            "income_total": 700,
            "expense_total": 240,
            "net_income": 460,
            "guest_groups_served": 5,
        })
        self.assertEqual(s.total_reviews, 8)
        self.assertEqual(s.total_rating_sum, 33)
        self.assertEqual(s.today_income["accommodation"], 300)
        self.assertEqual(s.today_income["entertainment"], 80)
        self.assertEqual(s.today_events, ["测试事件A", "测试事件B"])
        self.assertEqual(s.decisions_left, 1)
        self.assertEqual(s.improve_service_uses_today, 2)
        self.assertEqual(s.food_stock, 17)
        self.assertEqual(s.reservations, [{
            "npc_id": 42,
            "group_size": 3,
            "visit_type": "overnight",
            "arrival_day": 4,
            "status": "accepted",
            "tent_id": 5,
            "paid": True,
        }])
        self.assertTrue(s.greenery_processed_today)
        self.assertEqual(s.day_to_overnight_cache, ["转过夜缓存事件"])
        self.assertEqual(s.day_campsite_groups_served, 7)
        self.assertEqual(s.pending_turn_plan, {
            "target_day": 3,
            "target_turn": 4,
            "free_actions": [{"action": "clean_tents", "tent_ids": [1]}],
            "actions": [{"action": "repair_tent", "tent_id": 2}],
        })
        self.assertEqual(s.pending_reviews, [{
            "created_day": 2,
            "rating": 4,
            "npc_id": 7,
            "visit_type": "overnight",
            "group_size": 2,
            "comment": "整体不错，是一次挺舒服的体验。",
        }])
        self.assertEqual(s.review_history, [{
            "created_day": 1,
            "rating": 5,
            "npc_id": 6,
            "visit_type": "day",
            "group_size": 1,
            "comment": "很满意，下次还想再来。",
        }])

        # 帐篷键恢复为 int
        self.assertIn(2, restored.tents)
        self.assertNotIn("2", restored.tents)
        t2 = restored.tents[2]
        self.assertEqual(t2.status, "occupied")
        self.assertFalse(t2.is_unlocked)
        self.assertEqual(t2.occupied_by, 99)
        self.assertEqual(t2.next_breakdown_turn, 123)
        self.assertTrue(restored.tents[1].is_unlocked)
        self.assertTrue(restored.tents[5].is_unlocked)
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
        self.assertTrue(n.review_attempted)
        self.assertEqual(n.economic_level, 2)
        self.assertEqual(n.spending_habit, 0)
        self.assertEqual(n.temperament, 1)
        self.assertEqual(n.visit_count, 3)
        self.assertEqual(n.last_visit_day, 1)
        self.assertEqual(n.last_dining_day, 3)
        self.assertEqual(n.checkout_turn, 2)
        self.assertTrue(n.is_reserved)
        self.assertTrue(n.paid)

        self.assertEqual(restored._npc_id_counter, 42)
        self.assertEqual(len(restored.npc_history), 1)
        self.assertEqual(restored.npc_history[0]["id"], 1)
        self.assertEqual(restored.npc_history[0]["last_visit_day"], 2)

    def test_opening_food_gift_persists_without_duplication(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        opening_events = list(engine.state.today_events)
        opening_stock = engine.state.food_stock

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.food_stock, opening_stock)
        self.assertEqual(restored.state.today_events, opening_events)
        self.assertEqual(len(restored.state.today_events), 1)


class EventHistoryPersistenceTests(PersistenceTestCase):
    def test_event_history_roundtrip(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine._append_event_history(1, 6, "预购小包，金币 -80", "action")
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(
            restored.state.event_history,
            [{
                "day": 1,
                "turn": 6,
                "text": "预购小包，金币 -80",
                "kind": "action",
            }],
        )


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
        self.assertEqual(
            engine.state.food_stock,
            CampingPlazaEngine.FOOD_PACKAGES["medium"]["portions"],
        )
        # 回退后自动写入有效快照
        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0][1])
        self.assertEqual(payload["snapshot_version"], 1)


class MissingSnapshotTableTests(PersistenceTestCase):
    def test_existing_database_without_snapshot_table_raises_and_preserves_schema(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE other_state (id INTEGER PRIMARY KEY, note TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO other_state (id, note) VALUES (1, 'keep-me')"
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(self._table_names(), ["other_state"])

        with self.assertRaisesRegex(
            RuntimeError,
            "存档加载失败，游戏已停止启动，以避免覆盖现有存档。",
        ):
            CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(self._table_names(), ["other_state"])
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT note FROM other_state WHERE id = 1"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "keep-me")


class CorruptJsonFallbackTests(PersistenceTestCase):
    """损坏 JSON 不应被误判为新游戏"""

    def test_corrupt_snapshot_raises_and_preserves_database(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(engine.save_state())

        # 手动写入非法 JSON
        broken_snapshot = "{not valid json !!!"
        self._write_snapshot_json(broken_snapshot)

        with self.assertRaisesRegex(
            RuntimeError,
            "存档加载失败，游戏已停止启动，以避免覆盖现有存档。",
        ):
            CampingPlazaEngine(db_path=self.db_path)

        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], broken_snapshot)


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
    """合法 JSON 但嵌套结构损坏时不应误发礼包"""

    def test_nested_tent_corruption_raises_without_default_game(self):
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

        with self.assertRaisesRegex(
            RuntimeError,
            "存档加载失败，游戏已停止启动，以避免覆盖现有存档。",
        ):
            CampingPlazaEngine(db_path=self.db_path)

        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        current_payload = json.loads(rows[0][1])
        self.assertEqual(current_payload["state"]["day"], 99)
        self.assertEqual(current_payload["state"]["balance"], 777)
        self.assertEqual(current_payload["tents"]["2"], {"missing_id": True})


class SnapshotVersionMismatchTests(PersistenceTestCase):
    """快照版本号与当前版本不一致时完整回退新游戏"""

    def test_old_snapshot_version_raises_and_preserves_snapshot(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        valid_payload["snapshot_version"] = 999
        valid_payload["state"]["day"] = 88
        valid_payload["state"]["balance"] = 12345
        self._write_snapshot_dict(valid_payload)

        with self.assertRaisesRegex(
            RuntimeError,
            "存档加载失败，游戏已停止启动，以避免覆盖现有存档。",
        ):
            CampingPlazaEngine(db_path=self.db_path)

        rows = self._snapshot_rows()
        self.assertEqual(len(rows), 1)
        current_payload = json.loads(rows[0][1])
        self.assertEqual(current_payload["snapshot_version"], 999)
        self.assertEqual(current_payload["state"]["day"], 88)
        self.assertEqual(current_payload["state"]["balance"], 12345)


class MissingDayCampsiteFieldFallbackTests(PersistenceTestCase):
    """旧快照缺少日间营位计数字段时安全回退到默认值"""

    def test_missing_day_campsite_groups_served_defaults_to_zero(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        del valid_payload["state"]["day_campsite_groups_served"]
        valid_payload["state"]["day"] = 4
        valid_payload["state"]["turn"] = 3
        self._write_snapshot_dict(valid_payload)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.day, 4)
        self.assertEqual(restored.state.turn, 3)
        self.assertEqual(restored.state.day_campsite_groups_served, 0)


class MissingTentUnlockedFieldFallbackTests(PersistenceTestCase):
    def test_missing_tent_unlocked_fields_default_to_new_unlock_rule(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        for tent_data in valid_payload["tents"].values():
            tent_data.pop("is_unlocked", None)

        self._write_snapshot_dict(valid_payload)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(restored.tents[1].is_unlocked)
        for tent_id in range(2, 7):
            self.assertFalse(restored.tents[tent_id].is_unlocked)


class DiningPersistenceTests(PersistenceTestCase):
    def test_last_dining_day_roundtrip_and_same_day_protection_survives_restore(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
            last_dining_day=engine.state.day,
        )
        engine.npc_pool.append(npc)
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        restored_npc = restored.npc_pool[0]

        self.assertEqual(restored_npc.last_dining_day, restored.state.day)
        restored._process_dining({"events": []})
        self.assertEqual(restored.state.today_income["dining"], 0)

    def test_restored_npc_can_consume_again_on_next_day(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=1,
            visit_type="day",
            location="dining",
            economic_level=1,
            spending_habit=1,
            last_dining_day=engine.state.day,
        )
        engine.npc_pool.append(npc)
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        restored._new_day()

        with unittest.mock.patch("game_engine.random.random", return_value=0.0):
            restored._process_dining({"events": []})

        self.assertEqual(restored.state.today_income["dining"], 30)
        self.assertEqual(restored.npc_pool[0].last_dining_day, restored.state.day)


class MissingLastDiningDayFallbackTests(PersistenceTestCase):
    def test_missing_last_dining_day_defaults_to_zero(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        valid_payload["npc_pool"] = [{
            "id": 7,
            "group_size": 2,
            "visit_type": "day",
            "arrival_turn": 1,
            "location": "dining",
            "total_satisfaction": 65,
            "has_left": False,
            "review_left": False,
            "review_rating": 0,
            "economic_level": 1,
            "spending_habit": 1,
            "temperament": 1,
            "visit_count": 1,
            "last_visit_day": 1,
            "is_reserved": False,
            "paid": False
        }]
        self._write_snapshot_dict(valid_payload)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.npc_pool[0].last_dining_day, 0)


class PendingReviewPersistenceTests(PersistenceTestCase):
    def test_missing_pending_reviews_defaults_to_empty_list(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        valid_payload["state"].pop("pending_reviews", None)
        self._write_snapshot_dict(valid_payload)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(restored.state.pending_reviews, [])

    def test_pending_reviews_do_not_settle_during_restore(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine.state.day = 2
        engine.state.turn = 2
        engine.state.pending_reviews = [{
            "created_day": 1,
            "rating": 4,
            "npc_id": 3,
            "visit_type": "day",
            "group_size": 2,
        }]
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)

        self.assertEqual(restored.state.total_reviews, 0)
        self.assertEqual(restored.state.total_rating_sum, 0)
        self.assertEqual(len(restored.state.pending_reviews), 1)

    def test_settled_pending_reviews_do_not_reapply_after_save_and_restore(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine.state.day = 2
        engine.state.turn = 1
        engine.state.pending_reviews = [{
            "created_day": 1,
            "rating": 4,
            "npc_id": 3,
            "visit_type": "day",
            "group_size": 2,
        }]
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        restored.advance_turn()
        self.assertTrue(restored.save_state())

        reloaded = CampingPlazaEngine(db_path=self.db_path)
        self.assertEqual(reloaded.state.total_reviews, 1)
        self.assertEqual(reloaded.state.total_rating_sum, 4)
        self.assertEqual(reloaded.state.pending_reviews, [])


class TurnPlanPersistenceTests(PersistenceTestCase):
    def test_missing_pending_turn_plan_defaults_to_none(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        original_rows = self._snapshot_rows()
        valid_payload = json.loads(original_rows[0][1])

        valid_payload["state"].pop("pending_turn_plan", None)
        self._write_snapshot_dict(valid_payload)

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertIsNone(restored.state.pending_turn_plan)

    def test_pending_turn_plan_restores_and_executes_once(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine.state.day = 1
        engine.state.turn = 2
        engine.tents[1].status = "broken"
        engine.tents[1].next_breakdown_turn = 99999
        engine.state.pending_turn_plan = {
            "target_day": 1,
            "target_turn": 2,
            "free_actions": [],
            "actions": [{"action": "repair_tent", "tent_id": 1}],
        }
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertIsNotNone(restored.state.pending_turn_plan)

        with unittest.mock.patch.object(CampingPlazaEngine, "_process_checkout_all"):
            with unittest.mock.patch.object(CampingPlazaEngine, "_process_checkin"):
                with unittest.mock.patch.object(CampingPlazaEngine, "_process_dining"):
                    with unittest.mock.patch.object(CampingPlazaEngine, "_process_entertainment"):
                        with unittest.mock.patch.object(CampingPlazaEngine, "_handle_breakdowns"):
                            result = restored.advance_turn()

        self.assertTrue(result["plan_execution"]["actions"][0]["success"])
        self.assertEqual(restored.tents[1].status, "available")
        self.assertIsNone(restored.state.pending_turn_plan)
        self.assertTrue(restored.save_state())

        reloaded = CampingPlazaEngine(db_path=self.db_path)
        second = reloaded.advance_turn()
        self.assertEqual(second["turn"], 3)
        self.assertIn("submit turn plan first", second["events"])
        self.assertIsNone(reloaded.state.pending_turn_plan)

    def test_expired_pending_turn_plan_does_not_execute(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine.state.day = 1
        engine.state.turn = 2
        engine.tents[1].status = "broken"
        engine.state.pending_turn_plan = {
            "target_day": 1,
            "target_turn": 3,
            "free_actions": [],
            "actions": [{"action": "repair_tent", "tent_id": 1}],
        }
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        result = restored.advance_turn()

        self.assertEqual(result["turn"], 2)
        self.assertEqual(restored.tents[1].status, "broken")
        self.assertIsNone(restored.state.pending_turn_plan)

    def test_new_day_clears_old_pending_turn_plan(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        engine.state.day = 1
        engine.state.turn = 6
        engine.state.pending_turn_plan = {
            "target_day": 1,
            "target_turn": 2,
            "free_actions": [],
            "actions": [{"action": "repair_tent", "tent_id": 1}],
        }

        engine._new_day()

        self.assertIsNone(engine.state.pending_turn_plan)
        self.assertEqual(engine.state.day, 2)
        self.assertEqual(engine.state.turn, 1)


class BrokenTentPenaltyPersistenceTests(PersistenceTestCase):
    """broken 帐篷临时扣分标记的当前版本存档往返"""

    def test_broken_tent_penalty_roundtrip(self):
        engine = CampingPlazaEngine(db_path=self.db_path)
        npc = NPCGroup(
            id=engine._next_npc_id(),
            group_size=2,
            visit_type="overnight",
            total_satisfaction=60,
            broken_tent_penalty=2,
            had_food_shortage=True,
            had_tent_problem=True,
            received_service_boost=True,
        )
        engine.npc_pool.append(npc)
        self.assertTrue(engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        restored_npc = restored.npc_pool[0]

        self.assertEqual(restored_npc.id, npc.id)
        self.assertEqual(restored_npc.broken_tent_penalty, 2)
        self.assertEqual(restored_npc.total_satisfaction, 60)
        self.assertTrue(restored_npc.had_food_shortage)
        self.assertTrue(restored_npc.had_tent_problem)
        self.assertTrue(restored_npc.received_service_boost)


if __name__ == "__main__":
    unittest.main()
