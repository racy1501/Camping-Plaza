import sys
import unittest

sys.path.insert(0, "camping_plaza")

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class TentCleaningFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None

    def test_broken_checkout_repair_still_requires_cleaning(self):
        self.engine.state.turn = 6
        tent = self.engine.tents[1]
        tent.status = "broken"
        tent.occupied_by = 1
        guest = NPCGroup(id=1, group_size=2, visit_type="overnight")
        self.engine.npc_pool.append(guest)

        self.engine._checkout_npc(guest, {"events": []})
        self.assertEqual(tent.status, "broken")
        self.assertTrue(tent.needs_cleaning)

        repaired = self.engine.repair_tent(1, consume_decision=False)
        self.assertTrue(repaired["success"])
        self.assertEqual(tent.status, "cleaning")
        self.assertTrue(tent.needs_cleaning)

        cleaned = self.engine.clean_tents([1])
        self.assertTrue(cleaned["success"])
        self.assertEqual(tent.status, "available")
        self.assertFalse(tent.needs_cleaning)

    def test_waiting_reservation_checks_in_same_turn_after_cleaning(self):
        self.engine.state.day = 2
        self.engine.state.turn = 2
        self.engine.state.today_arrival_plan_day = 2
        tent = self.engine.tents[1]
        tent.status = "cleaning"
        tent.needs_cleaning = True
        guest = NPCGroup(id=101, group_size=2, visit_type="overnight", is_reserved=True, paid=True)
        entry = self.engine._build_arrival_plan_entry(guest, 2, "reservation", tent_id=1)
        self.engine.state.today_arrival_plan = [entry]

        self.engine._process_planned_arrivals({"events": []})
        self.assertEqual(entry["arrival_status"], "pending")
        self.assertEqual(self.engine.get_waiting_cleaning_checkin_tent_ids(), [1])

        self.assertTrue(self.engine.clean_tents([1])["success"])
        self.engine._process_planned_arrivals({"events": []})
        self.assertEqual(entry["arrival_status"], "arrived")
        self.assertEqual(tent.status, "occupied")

    def test_day_end_can_repay_and_clean_in_one_submission(self):
        self.engine.state.day = 26
        self.engine.state.turn = 6
        self.engine.state.startup_debt_settlement_completed = True
        self.engine.state.balance = 1000
        self.engine.state.decisions_left = 3
        self.engine.tents[1].status = "cleaning"
        self.engine.tents[1].needs_cleaning = True

        result = self.engine.submit_day_end_actions([
            {"action": "repay_debt", "params": {"amount": 200}},
            {"action": "clean_tents", "params": {"tent_ids": [1]}},
        ])

        self.assertTrue(result["success"])
        self.assertTrue(all(item["success"] for item in result["results"]))
        self.assertEqual(self.engine.state.balance, 800)
        self.assertEqual(self.engine.state.debt_remaining, 20800)
        self.assertEqual(self.engine.state.decisions_left, 3)
        self.assertEqual(self.engine.tents[1].status, "available")
        self.assertTrue(self.engine.state.day_end_completed)


class Turn6McpFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(db_path=":memory:")
        self.engine.state.today_conflict_event = None
        self.original_engine = game_api.engine
        game_api.engine = self.engine

    def tearDown(self):
        game_api.engine = self.original_engine

    def test_turn6_returns_summary_unified_day_end_actions_and_queries(self):
        self.engine.state.day = 26
        self.engine.state.turn = 6
        self.engine.state.startup_debt_settlement_completed = True
        self.engine.tents[1].status = "broken"
        self.engine.tents[1].needs_cleaning = True
        self.engine.state.event_history.append({
            "day": 26,
            "event_type": "food_discard",
            "data": {"portions": 3},
        })

        response = game_api.mcp_available_actions()
        self.assertEqual(response["available_actions"][0]["action"], "submit_day_end_actions")
        entry = response["available_actions"][0]
        candidates = entry["day_end_action_candidates"]
        self.assertTrue(any(
            item["action"] == "repay_debt" for item in candidates
        ))
        self.assertTrue(any(
            item["action"] == "clean_tents"
            and item["params"] == {"tent_ids": [1]}
            for item in candidates
        ))
        self.assertEqual(response["decision_summary"]["broken_tents"][0]["tent_id"], 1)
        self.assertEqual(response["decision_summary"]["food_discarded_portions"], 3)
        self.assertIn("食材已废弃", response["decision_summary"]["alerts"][0])
        self.assertNotIn("available_queries", response)

        self.engine.state.day_end_completed = True
        confirmed = game_api.mcp_available_actions()["available_actions"]
        self.assertEqual(confirmed[0]["action"], "start_next_day")
        self.assertEqual(confirmed[0]["endpoint"], "/api/day/start")
        self.assertNotIn("decision_summary", game_api.mcp_available_actions())

    def test_turn6_state_omits_expired_food_stock_but_keeps_discard_summary(self):
        self.engine.state.turn = 6
        self.engine.state.food_stock = 0
        self.engine.state.event_history.append({
            "day": 1,
            "event_type": "food_discard",
            "data": {"portions": 4},
        })

        state = game_api.mcp_state()

        self.assertNotIn("food_stock", state)
        self.assertIn("food_discarded_portions", game_api.mcp_available_actions()["decision_summary"])

    def test_mcp_state_keeps_food_stock_before_business_ends(self):
        self.engine.state.food_stock = 9
        for turn in (1, 2, 3, 4, 5):
            with self.subTest(turn=turn):
                self.engine.state.turn = turn
                self.assertEqual(game_api.mcp_state()["food_stock"], 9)


if __name__ == "__main__":
    unittest.main()
