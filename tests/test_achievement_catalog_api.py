"""成就图鉴与每日限次动作的定向测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine


class AchievementCatalogEngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")

    def _achievement(self, achievement_id):
        catalog = self.engine.get_achievement_catalog()["achievements"]
        return next(item for item in catalog if item["id"] == achievement_id)

    def test_normal_achievement_order_hints_and_conditions_are_formal(self):
        expected = [
            ("first_served_group", "真来人了", "营地开始运转。", "首次成功接待一组客人。"),
            ("first_day_complete", "老板上线", "欢迎光临！", "Day 1 正式结束并进入 Day 2。"),
            ("first_overnight_group", "今晚住这儿", "有人愿意把一晚留在营地。", "首次成功接待一组过夜客。"),
            ("first_day_to_overnight", "不着急走", "一次比原计划更久的停留。", "首次有日间客成功转为过夜客。"),
            ("first_tip", "还有小费！", "客人离开时多留下的一点心意。", "首次收到客人小费。"),
            ("tent_2_purchased", "地盘 +1", "开启新空间。", "购买 2 号帐篷。"),
            ("all_tents_unlocked", "都住得下", "空间全开。", "解锁全部 6 顶帐篷。"),
            ("dining_lv1", "先吃饭吧", "美食more more。", "首次升级餐饮至 Lv1。"),
            ("entertainment_lv1", "有得玩了", "丰富一下娱乐活动。", "首次升级娱乐至 Lv1。"),
            ("greenery_lv1", "有点绿了", "营地第一次有了更像样的绿意。", "首次升级绿化至 Lv1。"),
            ("all_normal_growth_complete", "差不多齐活", "营地的常规建设逐渐接近完整。", "完成温泉之前全部 11 个普通成长节点。"),
            ("hot_spring_built", "开泡", "营地迎来一项更大的设施。", "建成温泉。"),
            ("served_groups_50", "客人来了", "营地渐渐有了稳定的人气。", "累计成功接待 50 组客人。"),
            ("served_groups_100", "越来越热闹", "这份人气继续往上积累。", "累计成功接待 100 组客人。"),
            ("served_groups_150", "生意兴隆", "营地真正热闹起来以后。", "累计成功接待 150 组客人。"),
        ]
        catalog = self.engine.get_achievement_catalog()["achievements"]
        normal = [
            item for item in catalog
            if item["id"] not in self.engine.DEBT_RESULT_ACHIEVEMENT_IDS
            and item["id"] != "bad_luck_breakdowns"
        ]
        self.assertEqual(
            [(item["id"], item["title"], item["description"]) for item in normal],
            [(achievement_id, title, hint) for achievement_id, title, hint, _ in expected],
        )
        for achievement_id, title, hint, condition in expected:
            self.assertEqual(self._achievement(achievement_id)["title"], title)
            self.assertEqual(self._achievement(achievement_id)["description"], hint)
            self.engine._unlock_achievement(achievement_id)
            unlocked = self._achievement(achievement_id)
            self.assertEqual(unlocked["title"], title)
            self.assertEqual(unlocked["description"], condition)

    def test_locked_normal_entries_show_hint_without_locked_title(self):
        catalog = self.engine.get_achievement_catalog()["achievements"]
        for achievement_id, definition in self.engine.ACHIEVEMENT_CATALOG.items():
            if (
                achievement_id in self.engine.DEBT_RESULT_ACHIEVEMENT_IDS
                or achievement_id == "bad_luck_breakdowns"
            ):
                continue
            achievement = next(item for item in catalog if item["id"] == achievement_id)
            self.assertEqual(achievement["status"], "locked")
            self.assertEqual(achievement["title"], definition["title"])
            self.assertEqual(achievement["description"], definition["hint"])
            self.assertNotIn("locked_title", definition)

    def test_bad_luck_breakdown_achievement_uses_its_special_hidden_display(self):
        before = self._achievement("bad_luck_breakdowns")
        self.assertEqual(before["status"], "hidden")
        self.assertEqual(before["title"], "今天是不是有点太衰了？")
        self.assertEqual(before["description"], "有些成就，还是别拿到比较好。")
        self.assertNotIn("坏事成双", before["title"])
        self.assertNotIn("同一个经营轮次", before["description"])

        self.engine._unlock_achievement("bad_luck_breakdowns")
        after = self._achievement("bad_luck_breakdowns")
        self.assertEqual(after["status"], "unlocked")
        self.assertEqual(after["title"], "坏事成双")
        self.assertEqual(
            after["description"],
            "同一个经营轮次内，新发生至少 2 顶帐篷故障。",
        )

    def test_debt_result_cards_are_hidden_then_revealed_as_two_outcomes(self):
        debt_ids = {
            "debt_paid_by_deadline",
            "debt_unpaid_by_deadline",
        }
        before_result = {
            item["id"]: item
            for item in self.engine.get_achievement_catalog()["achievements"]
            if item["id"] in debt_ids
        }
        self.assertEqual({item["status"] for item in before_result.values()}, {"hidden"})
        self.assertEqual({item["title"] for item in before_result.values()}, {"隐藏成就"})
        self.assertEqual({item["description"] for item in before_result.values()}, {""})

        self.engine.state.day = 26
        self.engine._unlock_achievement("debt_paid_by_deadline")
        after_result = {
            item["id"]: item
            for item in self.engine.get_achievement_catalog()["achievements"]
            if item["id"] in debt_ids
        }
        self.assertEqual(after_result["debt_paid_by_deadline"]["status"], "unlocked")
        self.assertEqual(after_result["debt_unpaid_by_deadline"]["status"], "alternative")
        for item in after_result.values():
            self.assertNotEqual(item["title"], "隐藏成就")
            self.assertTrue(item["description"])

    def test_daily_limit_candidates_count_down_and_disable_at_zero(self):
        self.engine.state.turn = 2

        def candidates():
            return {
                item["action"]: item
                for item in game_api._build_neutral_turn_action_candidates(self.engine)[
                    "decision_action_candidates"
                ]
            }

        self.assertEqual(
            (candidates()["improve_service"]["remaining_today"], candidates()["improve_service"]["daily_limit"]),
            (2, 2),
        )
        self.engine.improve_service(consume_decision=False)
        self.assertEqual(
            (candidates()["improve_service"]["remaining_today"], candidates()["improve_service"]["daily_limit"]),
            (1, 2),
        )
        self.engine.improve_service(consume_decision=False)
        self.assertEqual(
            (candidates()["improve_service"]["remaining_today"], candidates()["improve_service"]["daily_limit"]),
            (0, 2),
        )
        self.assertFalse(candidates()["improve_service"]["enabled"])

        self.assertEqual(
            (candidates()["clean_campsite"]["remaining_today"], candidates()["clean_campsite"]["daily_limit"]),
            (2, 2),
        )
        self.engine.clean_campsite(consume_decision=False)
        self.assertEqual(candidates()["clean_campsite"]["remaining_today"], 1)
        self.engine.clean_campsite(consume_decision=False)
        self.assertEqual(candidates()["clean_campsite"]["remaining_today"], 0)
        self.assertFalse(candidates()["clean_campsite"]["enabled"])

        self.assertEqual(
            (candidates()["make_post"]["remaining_today"], candidates()["make_post"]["daily_limit"]),
            (1, 1),
        )
        with mock.patch("game_engine.random.random", return_value=0.9):
            self.engine.make_post()
        self.assertEqual(
            (candidates()["make_post"]["remaining_today"], candidates()["make_post"]["daily_limit"]),
            (0, 1),
        )
        self.assertFalse(candidates()["make_post"]["enabled"])


class AchievementCatalogApiTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(
            dir=os.path.join(_PROJECT_ROOT, "tests"),
            prefix="achievement_catalog_",
            suffix=".db",
            delete=False,
        )
        self.db_path = handle.name
        handle.close()
        self.original_db_path = game_api.DB_PATH
        self.original_engine = game_api.engine
        self.database_url_patch = mock.patch.dict(os.environ, {"DATABASE_URL": ""})
        self.database_url_patch.start()
        game_api.DB_PATH = self.db_path
        game_api.engine = None
        self.client = TestClient(game_api.app)

    def tearDown(self):
        self.client.close()
        game_api.DB_PATH = self.original_db_path
        game_api.engine = self.original_engine
        self.database_url_patch.stop()
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except PermissionError:
                pass

    def _create_session(self):
        response = self.client.post("/api/session")
        self.assertEqual(response.status_code, 200)
        return response.json()["session_id"]

    def test_achievement_queries_are_session_isolated_and_regular_state_omits_catalog(self):
        session_a = self._create_session()
        session_b = self._create_session()
        engine_a = game_api.get_engine(session_a)
        engine_a._unlock_achievement("first_tip")
        engine_a.save_state()

        api_a = self.client.get("/api/achievements", params={"session_id": session_a})
        api_b = self.client.get("/api/achievements", params={"session_id": session_b})
        mcp_a = self.client.get("/mcp/achievements", params={"session_id": session_a})
        self.assertEqual(api_a.status_code, 200)
        self.assertEqual(api_b.status_code, 200)
        self.assertEqual(api_a.json(), mcp_a.json())
        self.assertEqual(api_a.json()["unlocked_count"], 1)
        self.assertEqual(api_b.json()["unlocked_count"], 0)

        state = self.client.get("/api/state", params={"session_id": session_a})
        mcp_state = self.client.get("/mcp/state", params={"session_id": session_a})
        self.assertNotIn("achievements", state.json())
        self.assertNotIn("achievements", mcp_state.json())

    def test_mcp_and_human_candidates_expose_both_limit_fields(self):
        session_id = self._create_session()
        engine = game_api.get_engine(session_id)
        engine.state.player_name = "测试"
        engine.state.turn = 2
        engine.state.improve_service_uses_today = 2
        engine.state.clean_campsite_uses_today = 2
        engine.state.post_used_today = True
        engine.save_state()

        mcp = self.client.get("/mcp/actions", params={"session_id": session_id}).json()
        submit_entry = next(
            item for item in mcp["available_actions"]
            if item["action"] == "execute_turn_plan"
        )
        mcp_candidates = {
            item["action"]: item
            for item in submit_entry["decision_action_candidates"]
        }
        human = self.client.get("/api/actions", params={"session_id": session_id}).json()
        human_candidates = {
            item["action"]: item
            for item in human["decision_action_candidates"]
        }
        for action, daily_limit in (
            ("improve_service", 2),
            ("clean_campsite", 2),
            ("make_post", 1),
        ):
            for candidate in (mcp_candidates[action], human_candidates[action]):
                self.assertEqual(candidate["remaining_today"], 0)
                self.assertEqual(candidate["daily_limit"], daily_limit)
                self.assertFalse(candidate["enabled"])
                self.assertTrue(candidate["reason"])


class AchievementCatalogFrontendTests(unittest.TestCase):
    def test_catalog_modal_and_counter_controls_are_wired_in_frontend(self):
        def read(relative_path):
            with open(os.path.join(_PROJECT_ROOT, relative_path), encoding="utf-8") as handle:
                return handle.read()

        index = read("camping_plaza/frontend/index.html")
        overview = read("camping_plaza/frontend/scripts/overview.js")
        styles = read("camping_plaza/frontend/styles/main.css")

        self.assertIn('id="achievementCatalogButton"', index)
        self.assertIn('id="achievementUnlockedCount"', index)
        self.assertIn('id="achievementModal"', index)
        self.assertIn('id="achievementModalClose"', index)
        self.assertIn('id="achievementGrid"', index)
        self.assertNotIn("/ 总数", index)
        self.assertIn("openAchievementCatalog", overview)
        self.assertIn("closeAchievementCatalog", overview)
        self.assertIn("sessionUrl('/api/achievements')", overview)
        self.assertIn("achievement_unlocked_count", overview)
        self.assertIn("candidate.remaining_today", overview)
        self.assertIn("candidate.daily_limit", overview)
        self.assertIn("btn.disabled = !candidate.enabled", overview)
        self.assertIn(".achievement-modal", styles)
        self.assertIn(".achievement-grid", styles)


if __name__ == "__main__":
    unittest.main()
