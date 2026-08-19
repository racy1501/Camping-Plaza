import sys
import unittest

sys.path.insert(0, "camping_plaza")

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class McpTurnOutputCompactionTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine

    def test_execute_turn_plan_returns_history_delta_not_raw_events(self):
        self.engine.state.turn = 2
        self.engine.state.today_events = ["内部逐客组流水，不应公开"]

        result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[], actions=[]
        ))

        self.assertTrue(result["success"])
        self.assertEqual((result["executed_day"], result["executed_turn"]), (1, 2))
        self.assertEqual((result["day"], result["turn"]), (1, 3))
        self.assertIn("events", result)
        self.assertNotIn("内部逐客组流水", str(result["events"]))
        self.assertIn("action_results", result)
        self.assertIn("balance_delta", result)
        self.assertIn("income_delta", result)
        self.assertNotIn("action_failures", result)
        self.assertNotIn("tents", result)
        self.assertNotIn("npc_id", str(result))

    def test_execute_turn_plan_returns_compact_submitted_action_result(self):
        self.engine.state.turn = 2
        self.engine.tents[1].status = "cleaning"

        result = game_api.submit_turn_plan(game_api.TurnPlanRequest(
            free_actions=[game_api.ActionRequest(
                action="clean_tents", params={"tent_ids": [1]}
            )],
            actions=[],
        ))

        self.assertEqual(result["action_results"], [{
            "action": "clean_tents", "success": True,
        }])
        self.assertNotEqual(self.engine.tents[1].status, "cleaning")

    def test_default_mcp_state_excludes_history_and_dashboard_fields(self):
        self.engine.state.turn = 3
        state = game_api.mcp_state()

        for field in (
            "arrival_plan", "event_history", "review_history", "today_events",
            "average_rating", "today_income", "greenery", "hot_spring",
            "day_campsite", "reservations", "turn_plan",
        ):
            self.assertNotIn(field, state)
        self.assertEqual(state["decisions_left"], 5)
        self.assertEqual(
            state["player_message"],
            "经营轮次 3/5｜剩余决策点 5",
        )
        self.assertIn("food_stock", state)

    def test_mcp_state_does_not_keep_reservation_context(self):
        self.engine.state.reservations = [{
            "group_size": 3, "visit_type": "overnight", "arrival_day": 2,
            "status": "accepted", "tent_id": 1,
        }]

        state = game_api.mcp_state()

        self.assertNotIn("reservations", state)
        self.assertNotIn("confirmed_reservations", state)

    def test_mcp_actions_omits_fixed_query_menu_and_enabled_reasons(self):
        self.engine.state.turn = 2
        response = game_api.mcp_available_actions()
        entry = response["available_actions"][0]

        self.assertNotIn("available_queries", response)
        self.assertEqual(
            entry["description"],
            "本轮所有操作须一次提交：free_actions + 0～3 项 actions；提交即进入下一 Turn。成功后已进入下一 Turn，普通连续经营优先读取下一 Turn 的 /mcp/actions。",
        )
        self.assertEqual(response["food_stock"], self.engine.state.food_stock)
        self.assertNotIn("confirmed_reservations", response)
        self.assertNotIn("reservation_summary", response)
        for candidate in entry["decision_action_candidates"]:
            if candidate["enabled"]:
                self.assertNotIn("reason", candidate)

    def _add_day_guests(self):
        self.engine.npc_pool.extend([
            NPCGroup(id=301, group_size=2, visit_type="day", location="campsite", campsite_slot=3),
            NPCGroup(id=302, group_size=2, visit_type="day", location="campsite", campsite_slot=5),
        ])

    def test_mcp_dining_summary_omits_satisfaction_and_food_details(self):
        self._add_day_guests()
        text = game_api._format_mcp_event(self.engine, {
            "event_type": "dining_completed",
            "guest_ids": [301, 302],
            "data": {"income": 120, "food_portions": 4, "satisfaction_gain": 6},
            "text": "3、5号营位客人完成用餐，收入120金币。",
        })

        self.assertEqual(text, "3、5号营位客人完成用餐，共收入120金币。")
        self.assertNotIn("满意度", text)
        self.assertNotIn("食材", text)

    def test_mcp_paid_entertainment_summary_uses_total_income_only(self):
        self._add_day_guests()
        text = game_api._format_mcp_event(self.engine, {
            "event_type": "entertainment_completed",
            "guest_ids": [301, 302],
            "data": {"items": [
                {"npc_id": 301, "activities": ["收费娱乐"], "tier_name": "基础娱乐", "income": 40, "satisfaction_gain": 2},
                {"npc_id": 302, "activities": ["收费娱乐"], "tier_name": "高级娱乐", "income": 90, "satisfaction_gain": 6},
            ]},
            "text": "人类前端完整文案",
        })

        self.assertEqual(text, "3、5号营位客人参加收费娱乐，共收入130金币。")
        self.assertNotIn("基础娱乐", text)
        self.assertNotIn("高级娱乐", text)
        self.assertNotIn("满意度", text)

    def test_mcp_free_entertainment_summary_has_no_zero_or_satisfaction(self):
        self._add_day_guests()
        text = game_api._format_mcp_event(self.engine, {
            "event_type": "entertainment_completed",
            "guest_ids": [301, 302],
            "data": {"items": [
                {"npc_id": 301, "activities": ["免费娱乐"], "income": 0, "satisfaction_gain": 1},
                {"npc_id": 302, "activities": ["免费娱乐"], "income": 0, "satisfaction_gain": 1},
            ]},
            "text": "人类前端完整文案",
        })

        self.assertEqual(text, "3、5号营位客人参加免费娱乐。")
        self.assertNotIn("满意度+1", text)
        self.assertNotIn("收入0", text)
        self.assertNotIn("收费0", text)

    def test_mcp_paid_and_free_same_guest_stays_compact(self):
        self._add_day_guests()
        text = game_api._format_mcp_event(self.engine, {
            "event_type": "entertainment_completed",
            "guest_ids": [301],
            "data": {"items": [
                {"npc_id": 301, "activities": ["收费娱乐", "免费娱乐"], "income": 40, "satisfaction_gain": 3},
            ]},
            "text": "人类前端完整文案",
        })

        self.assertEqual(text, "3号营位客人参加收费娱乐和免费娱乐，共收入40金币。")
        self.assertNotIn("满意度", text)

    def test_mcp_hot_spring_summary_omits_satisfaction(self):
        self._add_day_guests()
        text = game_api._format_mcp_event(self.engine, {
            "event_type": "hot_spring_completed",
            "guest_ids": [301, 302],
            "data": {"income": 240, "satisfaction_gain": 12},
            "text": "3、5号营位客人使用温泉，收入240金币，满意度+12。",
        })

        self.assertEqual(text, "3、5号营位客人使用温泉，共收入240金币。")
        self.assertNotIn("满意度", text)
        self.assertNotIn("满意度+12", text)

    def test_mcp_keeps_player_action_feedback_and_review_anonymity(self):
        self._add_day_guests()
        action_text = "服务提升，3、5号营位客人满意度+5。"
        self.assertEqual(game_api._format_mcp_event(self.engine, {
            "event_type": "improve_service", "text": action_text,
        }), action_text)
        self.assertEqual(game_api._format_mcp_event(self.engine, {
            "event_type": "review_pending", "data": {"count": 2},
            "guest_ids": [301, 302], "text": "旧文案",
        }), "有2组客人留下评价，将于次日晨间结算。")


if __name__ == "__main__":
    unittest.main()
