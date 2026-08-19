"""客人碎片剧情镜头的定向回归测试。"""

import os
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine, NPCGroup


class _MomentRandom:
    def random(self):
        return 0.0

    @staticmethod
    def choice(items):
        return items[0]


class GuestMomentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.engine = CampingPlazaEngine(db_path=os.path.join(self.temp_dir.name, "test.db"))
        for tent in self.engine.tents.values():
            tent.next_breakdown_turn = 999999
        self.engine.state.turn = 3
        self.engine.state.today_arrival_plan_day = self.engine.state.day

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            pass

    def _add_day_guest(self, npc_id=1, actions=None, arrival_status="arrived", temperament=1):
        npc = NPCGroup(
            id=npc_id,
            group_size=2,
            visit_type="day",
            location="campsite",
            campsite_slot=npc_id,
            temperament=temperament,
        )
        self.engine.npc_pool.append(npc)
        entry = {
            "npc_id": npc_id,
            "planned_day": self.engine.state.day,
            "arrival_status": arrival_status,
            "planned_actions": list(actions or []),
        }
        self.engine.state.today_arrival_plan.append(entry)
        return npc, entry

    def _record_forced_moment(self, snapshot):
        with mock.patch("game_engine.random.Random", return_value=_MomentRandom()):
            self.engine._record_guest_moment(
                snapshot, self.engine.state.day, self.engine.state.turn
            )

    def test_business_event_stays_separate_and_dining_uses_real_menu(self):
        _, entry = self._add_day_guest(actions=[
            {"action": "dining", "status": "pending", "menu_key": "standard"}
        ])
        snapshot = self.engine._snapshot_turn_business_state()
        entry["planned_actions"][0]["status"] = "completed"
        self.engine._record_business_event(
            self.engine.state.day,
            self.engine.state.turn,
            "dining_completed",
            guest_ids=[1],
            data={"income": 90},
        )
        balance_before = self.engine.state.balance
        food_before = self.engine.state.food_stock
        self._record_forced_moment(snapshot)
        events = [
            event for event in self.engine.state.event_history
            if event["event_type"] in {"dining_completed", "guest_moment"}
        ]
        self.assertEqual([event["event_type"] for event in events], ["dining_completed", "guest_moment"])
        self.assertEqual(events[0]["text"], "1号营位客人完成用餐，收入90金币。")
        self.assertIn("中档套餐", events[1]["text"])
        self.assertEqual((self.engine.state.balance, self.engine.state.food_stock), (balance_before, food_before))

    def test_single_turn_and_daily_limits_do_not_add_state_field(self):
        _, entry = self._add_day_guest(actions=[
            {"action": "dining", "status": "pending", "menu_key": "basic"}
        ])
        snapshot = self.engine._snapshot_turn_business_state()
        entry["planned_actions"][0]["status"] = "completed"
        self._record_forced_moment(snapshot)
        self._record_forced_moment(snapshot)
        self.assertEqual(
            len([event for event in self.engine.state.event_history if event["event_type"] == "guest_moment"]),
            1,
        )
        self.engine.state.event_history.clear()
        self.engine.state.event_sequence = 0
        for prior_turn in (1, 2):
            self.engine._append_event_history(
                self.engine.state.day, prior_turn, "已落盘镜头", "world", "guest_moment"
            )
        self.engine.state.turn = 4
        self._record_forced_moment(snapshot)
        self.assertEqual(
            len([event for event in self.engine.state.event_history if event["event_type"] == "guest_moment"]),
            2,
        )
        self.assertFalse(hasattr(self.engine.state, "guest_moment_generated"))
        self.assertFalse(hasattr(self.engine.state, "guest_moment_count"))

    def test_no_candidates_does_not_generate_or_reroll_persisted_text(self):
        snapshot = self.engine._snapshot_turn_business_state()
        event_count_before = len(self.engine.state.event_history)
        self._record_forced_moment(snapshot)
        self.assertEqual(len(self.engine.state.event_history), event_count_before)

        _, entry = self._add_day_guest(actions=[
            {"action": "dining", "status": "pending", "menu_key": "basic"}
        ])
        snapshot = self.engine._snapshot_turn_business_state()
        entry["planned_actions"][0]["status"] = "completed"
        self._record_forced_moment(snapshot)
        saved_text = self.engine.state.event_history[-1]["text"]
        self._record_forced_moment(snapshot)
        self.assertEqual(self.engine.state.event_history[-1]["text"], saved_text)
        self.assertEqual(
            len([event for event in self.engine.state.event_history if event["event_type"] == "guest_moment"]),
            1,
        )

    def test_no_dining_and_greenery_candidates_only_use_new_arrival_without_location_change(self):
        npc, entry = self._add_day_guest(actions=[], arrival_status="planned")
        snapshot = self.engine._snapshot_turn_business_state()
        entry["arrival_status"] = "arrived"
        npc.greenery_entry_bonus_applied = True
        self.engine.facilities["greenery"].level = 1
        location_before = npc.location
        candidates = self.engine._build_guest_moment_candidates(
            snapshot, self.engine.state.day, self.engine.state.turn
        )
        self.assertEqual({item["source"] for item in candidates}, {"no_dining", "greenery"})
        self.assertEqual(npc.location, location_before)

    def test_entertainment_and_shortage_only_reference_completed_or_failed_actions(self):
        _, entertainment_entry = self._add_day_guest(
            npc_id=1,
            actions=[
                {"action": "paid_entertainment", "status": "pending", "tier_key": "premium"},
                {"action": "free_entertainment", "status": "pending"},
            ],
        )
        _, shortage_entry = self._add_day_guest(
            npc_id=2,
            actions=[{"action": "dining", "status": "pending", "menu_key": "basic"}],
            temperament=2,
        )
        snapshot = self.engine._snapshot_turn_business_state()
        entertainment_entry["planned_actions"][0]["status"] = "completed"
        entertainment_entry["planned_actions"][1]["status"] = "completed"
        shortage_entry["planned_actions"][0]["status"] = "waiting_for_restock"
        candidates = self.engine._build_guest_moment_candidates(
            snapshot, self.engine.state.day, self.engine.state.turn
        )
        by_source = {item["source"]: item for item in candidates}
        self.assertIn("便携 K 歌设备租赁", by_source["entertainment"]["texts"][0])
        shortage_text = by_source["dining_shortage"]["texts"][0]
        self.assertNotIn("temperament", shortage_text)
        self.assertNotIn("暴躁", shortage_text)

    def test_conflict_and_day_to_overnight_are_real_success_only(self):
        npc, _ = self._add_day_guest()
        snapshot = self.engine._snapshot_turn_business_state()
        self.assertNotIn(
            "day_to_overnight",
            {item["source"] for item in self.engine._build_guest_moment_candidates(snapshot, 1, 3)},
        )
        self.engine.tents[1].is_unlocked = True
        self.engine.tents[1].status = "occupied"
        self.engine.tents[1].occupied_by = npc.id
        npc.visit_type = "overnight"
        npc.location = "tent_1"
        self.engine._record_business_event(
            1, 3, "temporary_conflict", guest_ids=[npc.id],
            data={"choice": "verbal", "affected_guest_ids": []}, merge=False,
        )
        candidates = self.engine._build_guest_moment_candidates(snapshot, 1, 3)
        sources = {item["source"] for item in candidates}
        self.assertIn("day_to_overnight", sources)
        self.assertIn("temporary_conflict", sources)
        self._record_forced_moment(snapshot)
        event_types = [event["event_type"] for event in self.engine.state.event_history]
        self.assertIn("temporary_conflict", event_types)
        self.assertIn("guest_moment", event_types)

    def test_event_text_falls_back_for_mcp_and_state_hides_hidden_tags(self):
        npc, _ = self._add_day_guest()
        npc.spending_habit = npc.economic_level = npc.temperament = 2
        event = {
            "event_type": "guest_moment",
            "text": "1号营位的客人在入口的绿植旁停了一会儿。",
        }
        self.assertEqual(game_api._format_mcp_event(self.engine, event), event["text"])
        previous_engine = game_api.engine
        game_api.engine = self.engine
        try:
            api_state = game_api.get_state()
            mcp_state = game_api.mcp_state()
        finally:
            game_api.engine = previous_engine
        visible_npc = api_state["active_npcs"][0]
        hidden_fields = {"spending_habit", "economic_level", "temperament"}
        self.assertFalse(hidden_fields & set(visible_npc))
        self.assertFalse(hidden_fields & set(mcp_state))


if __name__ == "__main__":
    unittest.main()
