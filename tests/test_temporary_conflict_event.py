"""NPC 临时矛盾事件的定向回归测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class TemporaryConflictEventTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.engine = CampingPlazaEngine(db_path=os.path.join(self.temp_dir.name, "test.db"))
        for tent in self.engine.tents.values():
            tent.next_breakdown_turn = 999999

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    def _add_planned_guests(self, count=2):
        self.engine.state.today_conflict_event = None
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            {"npc_id": index + 1, "planned_day": self.engine.state.day,
             "arrival_turn": 2, "temperament": index % 3}
            for index in range(count)
        ]

    def test_less_than_two_groups_sets_no_event_without_probability_roll(self):
        self._add_planned_guests(1)
        with mock.patch("game_engine.random.random") as random_mock:
            self.engine._initialize_today_conflict_event()
        self.assertEqual(self.engine.state.today_conflict_event, {"status": "no_event"})
        random_mock.assert_not_called()

    def test_scheduled_event_is_generated_once_with_fixed_results(self):
        self._add_planned_guests(2)
        with mock.patch("game_engine.random.random", side_effect=[0.0, 1.0, 1.0, 1.0, 1.0]), \
             mock.patch("game_engine.random.sample", return_value=self.engine.state.today_arrival_plan), \
             mock.patch("game_engine.random.randint", return_value=5):
            self.engine._initialize_today_conflict_event()
        event = self.engine.state.today_conflict_event
        self.assertEqual(event["status"], "scheduled")
        self.assertEqual(event["trigger_turn"], 5)
        self.assertEqual(event["verbal_result"], {"npc_a_delta": 0, "npc_b_delta": 0})
        self.assertEqual(event["gift_result"], {"npc_a_delta": 0, "npc_b_delta": 0})
        self.assertIn(event["topic"], self.engine.TEMPORARY_CONFLICT_TOPIC_TEMPLATES)
        self.assertIn(event["topic_variant"], range(
            len(self.engine.TEMPORARY_CONFLICT_TOPIC_TEMPLATES[event["topic"]])
        ))
        before = dict(event)
        self.engine._initialize_today_conflict_event()
        self.assertEqual(self.engine.state.today_conflict_event, before)

    def test_all_conflict_topics_have_player_visible_opening(self):
        for topic, variants in self.engine.TEMPORARY_CONFLICT_TOPIC_TEMPLATES.items():
            for variant in range(len(variants)):
                opening = self.engine._format_temporary_conflict_opening(
                    ["甲组", "乙组"], {"topic": topic, "topic_variant": variant}
                )
                self.assertIn(variants[variant], opening)
                self.assertNotEqual(opening, "甲组与乙组发生了争执。")

    def test_same_temperament_has_lower_conflict_weight_than_different(self):
        same = {"temperament": 1}
        different = {"temperament": 2}
        self.assertLess(
            self.engine._get_temporary_conflict_probability(same, same),
            self.engine._get_temporary_conflict_probability(same, different),
        )

    def test_resolution_probability_depends_only_on_each_npc_temperament(self):
        self.assertEqual(
            [self.engine._get_temporary_conflict_penalty_probability({"temperament": value}, "verbal") for value in range(3)],
            [0.10, 0.30, 0.55],
        )
        self.assertEqual(
            [self.engine._get_temporary_conflict_penalty_probability({"temperament": value}, "gift") for value in range(3)],
            [0.05, 0.15, 0.30],
        )

    def test_resolution_reads_pre_generated_result_without_new_random_roll(self):
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": -2, "npc_b_delta": 0},
        }
        with mock.patch("game_engine.random.random", side_effect=AssertionError("must not re-roll")):
            result = self.engine.resolve_current_temporary_conflict("verbal")

        self.assertTrue(result["success"])

    def test_topic_is_reused_in_resolution_and_history_without_reroll(self):
        self.engine.state.turn = 3
        self.engine.npc_pool.extend([
            NPCGroup(id=1, group_size=1, visit_type="day", campsite_slot=1),
            NPCGroup(id=2, group_size=1, visit_type="day", campsite_slot=2),
        ])
        event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3, "topic": "facility_queue", "topic_variant": 1,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }
        self.engine.state.today_conflict_event = event
        with mock.patch("game_engine.random.random", side_effect=AssertionError("must not re-roll topic")):
            result = self.engine.resolve_current_temporary_conflict("verbal")
        self.assertIn("公共设施前的先后顺序", result["message"])
        self.assertIn("公共设施前的先后顺序", self.engine.state.event_history[-1]["text"])
        self.assertEqual(self.engine.state.event_history[-1]["data"]["topic"], "facility_queue")

    def test_plan_requires_immediate_conflict_resolution_only_on_trigger_turn(self):
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }
        self.assertFalse(self.engine.submit_turn_plan([], [])["success"])
        self.engine.state.today_conflict_event = {"status": "no_event"}
        self.assertFalse(self.engine.submit_turn_plan([], [], "verbal")["success"])
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }
        self.assertTrue(self.engine.resolve_current_temporary_conflict("verbal")["success"])
        self.assertTrue(self.engine.submit_turn_plan([], [])["success"])

    def test_conflict_history_keeps_trigger_turn_after_state_advances(self):
        for trigger_turn in (2, 3, 4, 5):
            self.engine.state.turn = trigger_turn
            self.engine.npc_pool.extend([
                NPCGroup(id=1000 + trigger_turn * 2, group_size=1, visit_type="day", campsite_slot=1),
                NPCGroup(id=1001 + trigger_turn * 2, group_size=1, visit_type="day", campsite_slot=2),
            ])
            self.engine.state.today_conflict_event = {
                "status": "scheduled",
                "npc_a_id": 1000 + trigger_turn * 2,
                "npc_b_id": 1001 + trigger_turn * 2,
                "trigger_turn": trigger_turn,
                "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
                "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            }
            result = {"events": [], "conflict_choice": "verbal"}
            self.engine._apply_temporary_conflict_event(result)
            self.engine.state.turn = trigger_turn + 1
            entry = self.engine.state.event_history[-1]
            self.assertEqual(entry["turn"], trigger_turn)

    def test_verbal_and_gift_do_not_consume_daily_decisions(self):
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }
        actions = [
            {"action": "repair_tent", "tent_id": tent_id}
            for tent_id in (1, 2, 3)
        ]
        self.assertTrue(self.engine.resolve_current_temporary_conflict("verbal")["success"])
        accepted = self.engine.submit_turn_plan([], actions[:2])
        self.assertTrue(accepted["success"])
        self.assertEqual(self.engine.state.decisions_left, 3)

        self.engine.state.pending_turn_plan = None
        self.engine.state.decisions_left = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": 0},
        }
        balance_before = self.engine.state.balance
        self.assertTrue(self.engine.resolve_current_temporary_conflict("gift")["success"])
        self.assertEqual(self.engine.state.balance, balance_before - 40)
        self.assertEqual(self.engine.state.today_expenses["conflict_care"], 40)
        self.assertEqual(self.engine.state.decisions_left, 3)
        self.assertTrue(self.engine.submit_turn_plan([], actions)["success"])

    def test_mcp_event_choices_expose_cost_without_hidden_results(self):
        original_engine = game_api.engine
        game_api.engine = self.engine
        self.addCleanup(setattr, game_api, "engine", original_engine)
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2, "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": -2}, "gift_result": {"npc_b_delta": -2},
        }
        event = game_api.mcp_available_actions()["available_actions"][0]["temporary_event"]
        self.assertEqual(event["choices"][0]["cost"], 0)
        self.assertEqual(event["choices"][1]["cost"], 40)
        self.assertTrue(event["choices"][0]["effect"])
        self.assertNotIn("result", str(event))
        self.assertNotIn("delta", str(event))
        self.assertNotIn("temperament", str(event))
        self.assertNotIn("probability", str(event))
        human_event = game_api.get_human_actions()["temporary_event"]
        self.assertEqual(human_event["choices"][1]["cost"], 40)

    def test_gift_insufficient_balance_keeps_event_pending_without_charge(self):
        self.engine.state.turn = 3
        self.engine.state.balance = 39
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": -2, "npc_b_delta": -2},
        }

        result = self.engine.resolve_current_temporary_conflict("gift")

        self.assertFalse(result["success"])
        self.assertEqual(self.engine.state.balance, 39)
        self.assertEqual(self.engine.state.today_expenses["conflict_care"], 0)
        self.assertEqual(self.engine.state.today_conflict_event["status"], "scheduled")

    def test_gift_cost_is_not_refunded_when_both_guests_reject(self):
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": -2, "npc_b_delta": -2},
        }
        balance_before = self.engine.state.balance

        result = self.engine.resolve_current_temporary_conflict("gift")

        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.balance, balance_before - 40)
        self.assertEqual(self.engine.state.today_expenses["conflict_care"], 40)

    def test_settlement_uses_fixed_result_once_and_resolves_before_turn_five_departures(self):
        self.engine.state.turn = 5
        guests = [
            NPCGroup(id=1, group_size=1, visit_type="day", total_satisfaction=1),
            NPCGroup(id=2, group_size=1, visit_type="day", total_satisfaction=10),
        ]
        self.engine.npc_pool.extend(guests)
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            {"npc_id": guest.id, "planned_day": self.engine.state.day,
             "arrival_turn": 2, "arrival_status": "arrived", "planned_actions": []}
            for guest in guests
        ]
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2, "trigger_turn": 5,
            "verbal_result": {"npc_a_delta": -2, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": 0, "npc_b_delta": -2},
        }
        self.assertTrue(self.engine.resolve_current_temporary_conflict("verbal")["success"])
        self.assertTrue(self.engine.submit_turn_plan([], [])["success"])
        self.engine.advance_turn()
        self.assertEqual(guests[0].total_satisfaction, 0)
        self.assertEqual(guests[1].total_satisfaction, 10)
        self.assertEqual(self.engine.state.today_conflict_event["status"], "resolved")
        self.engine.advance_turn()
        self.assertEqual(guests[0].total_satisfaction, 0)

    def test_mcp_actions_exposes_compact_event_without_npc_ids(self):
        original_engine = game_api.engine
        game_api.engine = self.engine
        self.addCleanup(setattr, game_api, "engine", original_engine)
        self.engine.state.turn = 3
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2, "trigger_turn": 3,
            "verbal_result": {}, "gift_result": {},
        }
        entry = game_api.mcp_available_actions()["available_actions"][0]
        self.assertIn("temporary_event", entry)
        self.assertNotIn("npc_id", str(entry["temporary_event"]))
        self.assertEqual(entry["temporary_event"]["choices"][0]["value"], "verbal")

    def test_entering_turn_two_settles_arrivals_before_actions_without_repeat_charge(self):
        day_guest = NPCGroup(id=101, group_size=2, visit_type="day")
        overnight_guest = NPCGroup(id=102, group_size=2, visit_type="overnight")
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            self.engine._build_arrival_plan_entry(day_guest, 2, "natural_day"),
            self.engine._build_arrival_plan_entry(overnight_guest, 2, "natural_overnight"),
        ]
        balance_before = self.engine.state.balance

        self.engine.advance_turn()

        self.assertEqual(self.engine.state.turn, 2)
        self.assertEqual(
            [entry["arrival_status"] for entry in self.engine.state.today_arrival_plan],
            ["arrived", "arrived"],
        )
        self.assertIsInstance(self.engine._find_npc(101).campsite_slot, int)
        self.assertIsNotNone(self.engine._find_occupied_tent_for_npc(102))
        balance_after_arrival = self.engine.state.balance
        history_count = len(self.engine.state.event_history)

        self.engine._settle_current_turn_arrivals()

        self.assertGreater(balance_after_arrival, balance_before)
        self.assertEqual(self.engine.state.balance, balance_after_arrival)
        self.assertEqual(len(self.engine.state.event_history), history_count)

    def test_actions_and_mcp_reads_do_not_repeat_settled_arrivals(self):
        guest = NPCGroup(id=101, group_size=2, visit_type="day")
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            self.engine._build_arrival_plan_entry(guest, 2, "natural_day"),
        ]
        self.engine.advance_turn()
        original_engine = game_api.engine
        game_api.engine = self.engine
        self.addCleanup(setattr, game_api, "engine", original_engine)
        before = (self.engine.state.balance, len(self.engine.npc_pool), len(self.engine.state.event_history))

        game_api.get_human_actions()
        game_api.get_human_actions()
        game_api.mcp_available_actions()
        game_api.mcp_available_actions()

        self.assertEqual(
            (self.engine.state.balance, len(self.engine.npc_pool), len(self.engine.state.event_history)),
            before,
        )

    def test_entering_turns_three_and_four_settles_their_arrivals(self):
        guests = [
            NPCGroup(id=101, group_size=1, visit_type="day"),
            NPCGroup(id=102, group_size=1, visit_type="overnight"),
        ]
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            self.engine._build_arrival_plan_entry(guests[0], 3, "natural_day"),
            self.engine._build_arrival_plan_entry(guests[1], 4, "natural_overnight"),
        ]
        self.engine.state.today_conflict_event = {"status": "no_event"}
        self.engine.advance_turn()
        self.assertEqual(self.engine.state.turn, 2)
        self.assertTrue(self.engine.submit_turn_plan([], [])["success"])
        self.engine.advance_turn()
        self.assertEqual(self.engine.state.turn, 3)
        self.assertEqual(self.engine.state.today_arrival_plan[0]["arrival_status"], "arrived")
        if self.engine.get_current_temporary_conflict_event() is not None:
            self.assertTrue(self.engine.resolve_current_temporary_conflict("verbal")["success"])
        self.assertTrue(self.engine.submit_turn_plan([], [])["success"])
        self.engine.advance_turn()
        self.assertEqual(self.engine.state.turn, 4)
        self.assertEqual(self.engine.state.today_arrival_plan[1]["arrival_status"], "arrived")

    def test_conflict_settlement_writes_one_natural_result_log(self):
        guests = [
            NPCGroup(id=1, group_size=1, visit_type="day", campsite_slot=4),
            NPCGroup(id=2, group_size=1, visit_type="overnight"),
        ]
        self.engine.npc_pool.extend(guests)
        self.engine.tents[1].occupied_by = 2
        self.engine.tents[1].status = "occupied"
        self.engine.state.turn = 3
        for outcome, expected in [
            ({"npc_a_delta": 0, "npc_b_delta": 0}, "双方很快平静下来"),
            ({"npc_a_delta": -2, "npc_b_delta": 0}, "4号营位客人仍有些不满"),
            ({"npc_a_delta": 0, "npc_b_delta": -2}, "1号帐篷住客仍有些不满"),
            ({"npc_a_delta": -2, "npc_b_delta": -2}, "双方情绪都没有完全平复"),
        ]:
            guests[0].total_satisfaction = 60
            guests[1].total_satisfaction = 60
            self.engine.state.today_conflict_event = {
                "status": "scheduled", "npc_a_id": 1, "npc_b_id": 2,
                "trigger_turn": 3, "verbal_result": outcome, "gift_result": outcome,
            }
            result = {"events": [], "conflict_choice": "verbal"}
            history_before = len(self.engine.state.event_history)
            self.engine._apply_temporary_conflict_event(result)
            self.assertEqual(len(result["events"]), 1)
            self.assertIn(expected, result["events"][0])
            self.assertEqual(len(self.engine.state.event_history), history_before + 1)
            self.assertEqual(guests[0].total_satisfaction, 60 + outcome["npc_a_delta"])
            self.assertEqual(guests[1].total_satisfaction, 60 + outcome["npc_b_delta"])

    def _run_dining_turn_for_logging(self, guests, food_stock):
        self.engine.state.turn = 3
        self.engine.state.food_stock = food_stock
        for index, guest in enumerate(guests, start=1):
            guest.campsite_slot = index
        self.engine.npc_pool.extend(guests)
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            {
                "npc_id": guest.id,
                "planned_day": self.engine.state.day,
                "arrival_status": "arrived",
                "visit_type": guest.visit_type,
                "planned_actions": [
                    {"action": "dining", "menu_key": "basic", "status": "pending", "planned_turn": 3}
                ],
            }
            for guest in guests
        ]
        result = {"events": []}
        self.engine._process_dining(result)
        return self.engine.state.event_history

    def test_food_shortage_dining_is_logged_as_one_group_summary(self):
        history = self._run_dining_turn_for_logging(
            [NPCGroup(id=201, group_size=1, visit_type="day")], 0
        )
        self.assertEqual(
            [entry["text"] for entry in history if "因食材不足未能提供" in entry["text"]],
            ["1号营位客人想要用餐，但因食材不足未能提供。"],
        )

    def test_multiple_shortage_dining_actions_are_one_summary(self):
        guests = [
            NPCGroup(id=201, group_size=1, visit_type="day", campsite_slot=1),
            NPCGroup(id=202, group_size=1, visit_type="day", campsite_slot=2),
        ]
        self._set_current_turn_dining(guests, 0)
        self.engine._record_current_turn_dining_shortage_preview()
        self.engine._process_dining({"events": []})
        history = self.engine.state.event_history
        shortage_entries = [entry for entry in history if "因食材不足未能提供" in entry["text"]]
        self.assertEqual(len(shortage_entries), 1)
        self.assertEqual(shortage_entries[0]["guest_ids"], [201, 202])
        self.assertIn("1、2号营位客人", shortage_entries[0]["text"])

    def _set_current_turn_dining(self, guests, food_stock, turns=None):
        self.engine.state.turn = 3
        self.engine.state.food_stock = food_stock
        self.engine.npc_pool.extend(guests)
        self.engine.state.today_arrival_plan = [
            {
                "npc_id": guest.id,
                "planned_day": self.engine.state.day,
                "arrival_status": "arrived",
                "planned_actions": [{
                    "action": "dining", "menu_key": "basic", "status": "pending",
                    "planned_turn": (turns or {}).get(guest.id, 3),
                }],
            }
            for guest in guests
        ]

    def test_dining_shortage_preview_records_once_without_mutating_plan(self):
        guest = NPCGroup(id=301, group_size=2, visit_type="day", campsite_slot=8)
        self._set_current_turn_dining([guest], 0)

        self.engine._record_current_turn_dining_shortage_preview()
        self.engine._record_current_turn_dining_shortage_preview()

        shortages = [e for e in self.engine.state.event_history if e["event_type"] == "dining_shortage"]
        self.assertEqual(len(shortages), 1)
        self.assertIn("8号营位客人", shortages[0]["text"])
        self.assertEqual(self.engine.state.food_stock, 0)
        self.assertEqual(self.engine.state.today_arrival_plan[0]["planned_actions"][0]["status"], "pending")

    def test_dining_shortage_preview_ignores_future_turn_and_simulates_order(self):
        first = NPCGroup(id=302, group_size=2, visit_type="day", campsite_slot=1)
        second = NPCGroup(id=303, group_size=2, visit_type="day", campsite_slot=2)
        future = NPCGroup(id=304, group_size=1, visit_type="day", campsite_slot=3)
        self._set_current_turn_dining([first, second, future], 3, {304: 4})

        self.engine._record_current_turn_dining_shortage_preview()

        shortage = next(e for e in self.engine.state.event_history if e["event_type"] == "dining_shortage")
        self.assertEqual(shortage["guest_ids"], [303])

    def test_dining_shortage_preview_does_not_duplicate_formal_settlement(self):
        guest = NPCGroup(id=305, group_size=2, visit_type="day", campsite_slot=4)
        self._set_current_turn_dining([guest], 0)
        self.engine._record_current_turn_dining_shortage_preview()

        self.engine._process_dining({"events": []})

        shortages = [e for e in self.engine.state.event_history if e["event_type"] == "dining_shortage"]
        self.assertEqual(len(shortages), 1)

    def test_dining_plan_restock_completes_without_waiting_status(self):
        guest = NPCGroup(id=306, group_size=2, visit_type="day", campsite_slot=5)
        self._set_current_turn_dining([guest], 0)
        self.engine._record_current_turn_dining_shortage_preview()
        self.engine._buy_food_package("small")

        self.engine._process_dining({"events": []})

        action = self.engine.state.today_arrival_plan[0]["planned_actions"][0]
        self.assertEqual(action["status"], "completed")

    def test_partial_dining_success_and_food_shortage_keep_both_logs(self):
        history = self._run_dining_turn_for_logging(
            [
                NPCGroup(id=201, group_size=1, visit_type="day"),
                NPCGroup(id=202, group_size=1, visit_type="day"),
            ], 1
        )
        texts = [entry["text"] for entry in history]
        self.assertTrue(any("完成用餐" in text for text in texts))
        self.assertTrue(any("因食材不足未能提供" in text for text in texts))

    def test_no_planned_dining_does_not_create_shortage_log(self):
        self.engine.state.turn = 3
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [{
            "npc_id": 301,
            "planned_day": self.engine.state.day,
            "arrival_status": "arrived",
            "planned_actions": [],
        }]
        snapshot = self.engine._snapshot_turn_business_state()
        self.engine._append_turn_business_summaries(snapshot, self.engine.state.day, 3)
        self.assertFalse(any("因食材不足未能提供" in entry["text"] for entry in self.engine.state.event_history))

    def test_improve_service_feedback_uses_visible_guest_locations(self):
        day_guest = NPCGroup(id=401, group_size=1, visit_type="day", campsite_slot=5)
        overnight_guest = NPCGroup(id=402, group_size=1, visit_type="overnight")
        self.engine.tents[1].occupied_by = overnight_guest.id
        self.engine.tents[1].status = "occupied"
        self.engine.npc_pool.extend([day_guest, overnight_guest])
        self.engine.state.turn = 3
        with mock.patch("game_engine.random.random", side_effect=[0.0, 0.0]):
            result = self.engine.improve_service(consume_decision=False)

        self.assertEqual(
            result["message"],
            "服务提升，5号营位客人、1号帐篷住客满意度+5。",
        )
        self.assertNotIn("npc_id", result["message"])
        self.assertNotIn("组客人", result["message"])
        self.assertEqual(day_guest.total_satisfaction, 65)
        self.assertEqual(overnight_guest.total_satisfaction, 65)

    def test_improve_service_no_hit_keeps_existing_feedback(self):
        guest = NPCGroup(id=403, group_size=1, visit_type="day", campsite_slot=2)
        self.engine.npc_pool.append(guest)
        self.engine.state.turn = 3
        with mock.patch("game_engine.random.random", return_value=1.0):
            result = self.engine.improve_service(consume_decision=False)
        self.assertEqual(result["message"], "服务提升，0组客人满意度+5")
        events = [
            event for event in self.engine.state.event_history
            if event.get("actor") == "player" and event.get("action") == "improve_service"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["targets"], [])
        self.assertEqual(events[0].get("guest_ids", []), [])

    def test_clean_and_campfire_include_turn_four_arrival(self):
        guest = NPCGroup(id=501, group_size=1, visit_type="day")
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [
            self.engine._build_arrival_plan_entry(guest, 2, "natural_day")
        ]
        self.engine.advance_turn()
        self.engine.state.turn = 4
        with mock.patch("game_engine.random.random", return_value=0.0):
            clean = self.engine.clean_campsite(consume_decision=False)
            fire = self.engine.hold_campfire(consume_decision=False)
        self.assertTrue(clean["success"])
        self.assertTrue(fire["success"])
        self.assertIn(501, fire.get("affected_npc_ids", self.engine.state.campfire_affected_npc_ids))

    def test_campfire_and_stargazing_keep_sixty_percent_hit_rate(self):
        guest = NPCGroup(id=502, group_size=1, visit_type="day", campsite_slot=1)
        self.engine.npc_pool.append(guest)
        self.engine.state.turn = 4
        with mock.patch("game_engine.random.random", return_value=0.59):
            fire = self.engine.hold_campfire(consume_decision=False)
        self.assertIn(guest.id, fire.get("affected_npc_ids", self.engine.state.campfire_affected_npc_ids))

        self.engine.state.turn = 5
        with mock.patch("game_engine.random.random", return_value=0.59):
            sky = self.engine.go_stargazing(consume_decision=False)
        self.assertIn(guest.id, sky.get("affected_npc_ids", self.engine.state.stargazing_affected_npc_ids))

        self.engine.state.turn = 4
        with mock.patch("game_engine.random.random", return_value=0.60):
            fire = self.engine.hold_campfire(consume_decision=False)
        self.assertNotIn(guest.id, fire.get("affected_npc_ids", self.engine.state.campfire_affected_npc_ids))

    def test_clean_campsite_is_limited_to_two_daily_uses_and_one_plan_action(self):
        self.engine.state.turn = 3
        self.assertTrue(self.engine.clean_campsite(consume_decision=False)["success"])
        self.assertTrue(self.engine.clean_campsite(consume_decision=False)["success"])
        self.assertFalse(self.engine.clean_campsite(consume_decision=False)["success"])
        self.assertEqual(self.engine.state.clean_campsite_uses_today, 2)

        self.engine.state.clean_campsite_uses_today = 0
        self.assertFalse(self.engine.submit_turn_plan(
            [], [{"action": "clean_campsite"}, {"action": "clean_campsite"}]
        )["success"])

    def test_structured_log_keeps_actual_execution_order(self):
        self.engine.state.turn = 5
        self.engine._record_business_event(
            1, 5, "food_restock", data={"name": "基础食材包", "portions": 5}
        )
        self.engine._record_business_event(1, 5, "dining_completed", guest_ids=[])
        history = self.engine.state.event_history[-2:]
        self.assertLess(history[0]["sequence"], history[1]["sequence"])
        self.assertEqual(history[0]["turn"], history[1]["turn"])

    def test_restock_retry_uses_the_actual_completion_turn_after_restock_log(self):
        guest = NPCGroup(id=551, group_size=1, visit_type="day", campsite_slot=1)
        self.engine.npc_pool.append(guest)
        self.engine.state.turn = 5
        self.engine.state.today_arrival_plan = [{
            "npc_id": guest.id, "planned_day": self.engine.state.day,
            "arrival_status": "arrived", "planned_actions": [{
                "action": "dining", "menu_key": "basic", "planned_turn": 4,
                "status": "waiting_for_restock",
            }],
        }]
        self.engine.state.food_stock = 1
        self.engine._record_business_event(
            self.engine.state.day, 5, "food_restock",
            data={"name": "基础食材包", "portions": 1},
        )
        self.engine._retry_waiting_dining_after_restock({"events": []})
        history = self.engine.state.event_history[-2:]
        self.assertEqual([item["event_type"] for item in history], [
            "food_restock", "dining_completed",
        ])
        self.assertEqual([item["turn"] for item in history], [5, 5])

    def test_immediate_conflict_resolution_writes_once_without_consuming_decision_point(self):
        self.engine.state.turn = 3
        self.engine.npc_pool.extend([
            NPCGroup(id=801, group_size=1, visit_type="day", campsite_slot=1),
            NPCGroup(id=802, group_size=1, visit_type="day", campsite_slot=2),
        ])
        self.engine.state.today_conflict_event = {
            "status": "scheduled", "npc_a_id": 801, "npc_b_id": 802,
            "trigger_turn": 3,
            "verbal_result": {"npc_a_delta": 0, "npc_b_delta": 0},
            "gift_result": {"npc_a_delta": -2, "npc_b_delta": 0},
        }
        result = self.engine.resolve_current_temporary_conflict("verbal")
        self.assertTrue(result["success"])
        self.assertEqual(self.engine.state.decisions_left, 5)
        self.assertEqual(self.engine.state.today_conflict_event["status"], "resolved")
        entry = self.engine.state.event_history[-1]
        self.assertEqual((entry["turn"], entry["event_type"]), (3, "temporary_conflict"))

    def test_post_is_once_daily_and_miss_is_silent(self):
        self.engine.state.turn = 3
        with mock.patch("game_engine.random.random", return_value=0.99):
            first = self.engine.make_post()
        self.assertTrue(first["success"])
        self.assertEqual(first["message"], "帖子已发布")
        self.assertIsNone(self.engine.state.pending_post_reservation)
        self.assertFalse(any("预约" in entry["text"] for entry in self.engine.state.event_history))
        second = self.engine.make_post()
        self.assertFalse(second["success"])

    def test_post_success_enters_next_day_reservations_at_turn_six(self):
        self.engine.state.turn = 3
        with mock.patch("game_engine.random.random", side_effect=[0.0, 0.0]), mock.patch(
            "game_engine.random.randint", return_value=1
        ):
            self.assertTrue(self.engine.make_post()["success"])
        self.engine.state.turn = 6
        result = {"events": []}
        self.engine._finalize_post_reservation(result)
        self.assertEqual(len(self.engine.state.reservations), 1)
        self.assertEqual(self.engine.state.reservations[0]["status"], "accepted")
        self.assertTrue(any("帖子带来了一组明日预约" in text for text in result["events"]))
        self.engine.state.day += 1
        self.engine.state.today_arrival_plan_day = 0
        self.engine._ensure_today_arrival_plan()
        self.assertTrue(any(entry.get("source") == "reservation" for entry in self.engine.state.today_arrival_plan))

    def test_post_day_without_campsite_capacity_is_missed(self):
        self.engine.state.turn = 3
        target_day = self.engine.state.day + 1
        self.engine.state.reservations = [
            {
                "arrival_day": target_day,
                "visit_type": "day",
                "status": "accepted",
            }
            for _ in range(self.engine.DAY_CAMPSITE_CAPACITY)
        ]
        with mock.patch("game_engine.random.random", side_effect=[0.0, 0.0]), mock.patch(
            "game_engine.random.randint", return_value=1
        ):
            self.assertTrue(self.engine.make_post()["success"])
        result = {"events": []}
        self.engine.state.turn = 6
        self.engine._finalize_post_reservation(result)
        self.assertEqual(len(self.engine.state.reservations), self.engine.DAY_CAMPSITE_CAPACITY)
        self.assertEqual(
            result["events"],
            ["今日帖子带来一组明日预约请求，但明日日间营位已满，未能接下。"],
        )

    def test_post_overnight_without_resource_is_missed(self):
        self.engine.state.turn = 3
        for tent in self.engine.tents.values():
            tent.is_unlocked = False
        with mock.patch("game_engine.random.random", side_effect=[0.0, 0.99]), mock.patch(
            "game_engine.random.randint", return_value=1
        ):
            self.assertTrue(self.engine.make_post()["success"])
        result = {"events": []}
        self.engine.state.turn = 6
        self.engine._finalize_post_reservation(result)
        self.assertEqual(self.engine.state.reservations, [])
        self.assertEqual(
            result["events"],
            ["今日帖子带来一组明日预约请求，但没有可接待该客组的空闲帐篷，未能接下。"],
        )

    def test_tips_settle_once_and_zero_has_no_log(self):
        guest = NPCGroup(id=601, group_size=1, visit_type="day", campsite_slot=1)
        self.engine.npc_pool.append(guest)
        self.engine.state.turn = 5
        balance_before = self.engine.state.balance
        with mock.patch("game_engine.random.random", return_value=0.0):
            self.engine._settle_tips({"events": []})
        self.assertEqual(self.engine.state.today_income["tip"], 20)
        self.assertEqual(self.engine.state.balance, balance_before + 20)
        self.engine._settle_tips({"events": []})
        self.assertEqual(self.engine.state.today_income["tip"], 20)
        self.assertEqual(self.engine.state.balance, balance_before + 20)

        self.engine.state.today_income["tip"] = 0
        self.engine.state.today_tip_settled = False
        balance_before = self.engine.state.balance
        with mock.patch("game_engine.random.random", return_value=0.99):
            result = {"events": []}
            self.engine._settle_tips(result)
        self.assertEqual(result["events"], [])
        self.assertEqual(self.engine.state.balance, balance_before)

    def test_campfire_changes_only_affected_tip_probability_and_star_amount(self):
        guests = [
            NPCGroup(id=701, group_size=1, visit_type="day", campsite_slot=1),
            NPCGroup(id=702, group_size=1, visit_type="day", campsite_slot=2),
        ]
        self.engine.npc_pool.extend(guests)
        self.engine.state.campfire_affected_npc_ids = [701]
        self.engine.state.stargazing_affected_npc_ids = [702]
        self.engine.state.turn = 5
        balance_before = self.engine.state.balance
        satisfaction_before = {guest.id: guest.total_satisfaction for guest in guests}
        with mock.patch("game_engine.random.random", side_effect=[0.40, 0.0]):
            self.engine._settle_tips({"events": []})
        self.assertEqual(self.engine.state.today_income["tip"], 65)
        self.assertEqual(self.engine.state.balance, balance_before + 65)
        self.assertEqual(
            {guest.id: guest.total_satisfaction for guest in guests},
            satisfaction_before,
        )

    def test_successful_greenery_upgrade_skips_same_batch_maintenance(self):
        self.engine.state.turn = 6
        self.engine.state.balance = 500
        with mock.patch.object(
            self.engine,
            "purchase_growth_project",
            return_value={"success": True, "category": "greenery", "price": 100, "display_name": "绿化 Lv1"},
        ) as upgrade, mock.patch.object(self.engine, "manage_greenery") as maintain:
            result = self.engine.submit_day_end_actions([
                {"action": "manage_greenery", "params": {"action": "maintain"}},
                {"action": "purchase_growth_project", "params": {"project_id": "greenery_lv1"}},
            ])
        self.assertTrue(result["success"])
        upgrade.assert_called_once()
        maintain.assert_not_called()
        self.assertTrue(any("绿化升级已包含当日维护" in text for text in result["events"]))

    def test_failed_greenery_upgrade_does_not_swallow_maintenance(self):
        self.engine.state.turn = 6
        with mock.patch.object(
            self.engine,
            "purchase_growth_project",
            return_value={"success": False, "category": "greenery", "error_code": "growth_project_not_purchasable"},
        ), mock.patch.object(
            self.engine,
            "manage_greenery",
            return_value="绿化已打理，花费50金币",
        ) as maintain:
            result = self.engine.submit_day_end_actions([
                {"action": "purchase_growth_project", "params": {"project_id": "greenery_lv1"}},
                {"action": "manage_greenery", "params": {"action": "maintain"}},
            ])
        self.assertTrue(result["success"])
        maintain.assert_called_once()
        self.assertTrue(any(item["action"] == "manage_greenery" and item["success"] for item in result["results"]))


if __name__ == "__main__":
    unittest.main()
