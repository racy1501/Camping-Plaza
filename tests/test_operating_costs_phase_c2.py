"""Phase C2 自动经营耗损的定向测试。"""

import os
import sys
import tempfile
import unittest


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class OperatingCostsPhaseC2Tests(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory(
            dir=os.path.join(
                os.environ.get("TEMP")
                or os.environ.get("TMP")
                or tempfile.gettempdir(),
                "camping_plaza_fix_temp",
            )
        )
        self.addCleanup(self._temp_dir.cleanup)
        self.engine = CampingPlazaEngine(
            db_path=os.path.join(self._temp_dir.name, "operating_costs_test.db")
        )
        self.engine.state.today_arrival_plan = []
        self.engine.state.pending_turn_plan = None
        self.engine.state.today_conflict_event = None

    def _add_successful_overnight(self, npc_id, tent_id, *, entry_visit_type="overnight"):
        npc = NPCGroup(
            id=npc_id,
            group_size=1,
            visit_type="overnight",
            location=f"tent_{tent_id}",
        )
        self.engine.npc_pool.append(npc)
        tent = self.engine.tents[tent_id]
        tent.is_unlocked = True
        tent.status = "occupied"
        tent.occupied_by = npc_id
        self.engine.state.today_arrival_plan.append({
            "npc_id": npc_id,
            "planned_day": self.engine.state.day,
            "visit_type": entry_visit_type,
            "arrival_status": "arrived",
        })
        return npc

    def test_lodging_cost_uses_actual_tent_prices_for_all_successful_paths(self):
        self._add_successful_overnight(1, 1)  # 自然过夜
        self._add_successful_overnight(2, 3)  # 预约实际到达
        self._add_successful_overnight(3, 4, entry_visit_type="day")  # 日转过夜

        self.engine._settle_daily_operating_costs()

        expected = sum(
            int(self.engine.TENT_PRICES[tent_id] * 0.10)
            for tent_id in (1, 3, 4)
        )
        self.assertEqual(self.engine.state.today_expenses["lodging_consumables"], expected)
        self.assertEqual(self.engine.state.balance, 1000 - expected)

    def test_nonqualifying_and_previous_day_stays_are_not_charged(self):
        self._add_successful_overnight(1, 1)
        self.engine.state.today_arrival_plan[0]["planned_day"] = self.engine.state.day - 1

        self.engine.state.today_arrival_plan.extend([
            {"npc_id": 2, "planned_day": self.engine.state.day, "arrival_status": "pending"},
            {"npc_id": 3, "planned_day": self.engine.state.day, "arrival_status": "arrived"},
        ])
        self.engine.npc_pool.append(NPCGroup(id=2, group_size=1, visit_type="overnight"))
        self.engine.npc_pool.append(NPCGroup(id=3, group_size=1, visit_type="day"))

        self.engine._settle_daily_operating_costs()

        self.assertEqual(self.engine.state.today_expenses["lodging_consumables"], 0)
        self.assertEqual(self.engine.state.balance, 1000)

    def test_unbuilt_hot_spring_has_no_cost_and_built_zero_income_costs_100(self):
        self.engine._settle_daily_operating_costs()
        self.assertEqual(self.engine.state.today_expenses["hot_spring_operating"], 0)

        self.engine.state.hot_spring_built = True
        self.engine._settle_daily_operating_costs()

        self.assertEqual(self.engine.state.today_expenses["hot_spring_operating"], 100)
        self.assertEqual(self.engine.state.balance, 900)

    def test_built_hot_spring_cost_uses_today_ticket_income(self):
        self.engine.state.hot_spring_built = True
        self.engine.state.today_income["hot_spring"] = 400

        self.engine._settle_daily_operating_costs()

        self.assertEqual(self.engine.state.today_expenses["hot_spring_operating"], 180)
        self.assertEqual(self.engine.state.balance, 820)

    def test_turn_five_to_six_settles_once_before_turn_six(self):
        self._add_successful_overnight(1, 2)
        self.engine.state.turn = 5
        self.assertTrue(self.engine.submit_turn_plan([], [])["success"])

        result = self.engine.advance_turn()
        expected = int(self.engine.TENT_PRICES[2] * 0.10)

        self.assertEqual(result["turn"], 6)
        self.assertEqual(result["balance"], 1000 - expected)
        self.assertEqual(self.engine.state.today_expenses["lodging_consumables"], expected)

        again = self.engine.advance_turn()
        self.assertEqual(again["balance"], 1000 - expected)
        self.assertEqual(self.engine.state.today_expenses["lodging_consumables"], expected)

    def test_turn_six_hot_spring_purchase_does_not_backcharge_current_day(self):
        self.engine.state.turn = 5
        self.assertTrue(self.engine.submit_turn_plan([], [])["success"])

        self.engine.advance_turn()
        self.assertEqual(self.engine.state.today_expenses["hot_spring_operating"], 0)

        self.engine.state.hot_spring_built = True
        self.assertEqual(self.engine.state.today_expenses["hot_spring_operating"], 0)


if __name__ == "__main__":
    unittest.main()
