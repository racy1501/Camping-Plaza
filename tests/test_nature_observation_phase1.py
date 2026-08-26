import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

from game_engine import CampingPlazaEngine, NPCGroup


class NatureObservationPhase1Tests(unittest.TestCase):
    def setUp(self):
        self._db_dir = os.path.join(tempfile.gettempdir(), "camping_plaza_fix_temp")
        os.makedirs(self._db_dir, exist_ok=True)
        self.db_path = os.path.join(self._db_dir, "nature_observation_phase1.sqlite")
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass
        self.engine = CampingPlazaEngine(db_path=self.db_path)

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except FileNotFoundError:
            pass

    def _entry(self, *, npc_id=1, arrival_turn=2, observation_plan=None):
        return {
            "npc_id": npc_id,
            "planned_day": self.engine.state.day,
            "arrival_turn": arrival_turn,
            "arrival_status": "arrived",
            "planned_actions": [],
            "observation_plan": observation_plan,
        }

    def _active_npc(self, npc_id=1):
        npc = NPCGroup(id=npc_id, group_size=2, visit_type="day")
        self.engine.npc_pool.append(npc)
        return npc

    def test_catalog_is_the_formal_fixed_order_and_weight_structure(self):
        catalog = CampingPlazaEngine.INSECT_CATALOG
        self.assertEqual(len(catalog), 12)
        self.assertEqual(
            [insect["id"] for insect in catalog],
            [
                "ladybug", "white_butterfly", "dragonfly", "cicada", "mantis",
                "grasshopper", "swallowtail", "firefly", "longhorn_beetle",
                "stick_insect", "rhinoceros_beetle", "stag_beetle",
            ],
        )
        self.assertEqual([insect["weight"] for insect in catalog], [4] * 6 + [3] * 4 + [2] * 2)
        self.assertEqual(sum(insect["weight"] for insect in catalog), 40)

    def test_discovery_percent_is_derived_from_catalog_progress(self):
        ids = [insect["id"] for insect in CampingPlazaEngine.INSECT_CATALOG]
        for count, expected in ((0, 35), (2, 35), (3, 45), (5, 45), (6, 55), (8, 55), (9, 65), (12, 65)):
            with self.subTest(count=count):
                self.engine.state.discovered_insects = ids[:count]
                self.assertEqual(self.engine._get_nature_observation_discovery_percent(), expected)

    def test_result_pool_is_one_integer_draw_with_all_results(self):
        for discovery_percent, not_found_weight in ((35, 2600), (45, 2200), (55, 1800), (65, 1400)):
            with self.subTest(discovery_percent=discovery_percent):
                with mock.patch("game_engine.random.randrange", return_value=not_found_weight - 1) as roll:
                    self.assertEqual(self.engine._roll_nature_observation_result(discovery_percent), "not_found")
                roll.assert_called_once_with(4000)
                with mock.patch("game_engine.random.randrange", return_value=not_found_weight) as roll:
                    self.assertEqual(self.engine._roll_nature_observation_result(discovery_percent), "ladybug")
                roll.assert_called_once_with(4000)

    def test_unbuilt_station_leaves_observation_plan_empty(self):
        self.engine.state.day = 3
        self.engine.state.today_arrival_plan_day = 0
        with mock.patch.object(
            self.engine, "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ), mock.patch.object(self.engine, "_roll_arrival_turn", return_value=3):
            self.assertTrue(self.engine._ensure_today_arrival_plan())
        self.assertIsNone(self.engine.state.today_arrival_plan[0]["observation_plan"])

    def test_built_station_generates_independent_plan_without_occupying_actions(self):
        self.engine.state.day = 3
        self.engine.state.today_arrival_plan_day = 0
        self.engine.state.nature_observation_station_built = True
        with mock.patch.object(
            self.engine, "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ), mock.patch.object(self.engine, "_roll_arrival_turn", return_value=3), \
             mock.patch("game_engine.random.randrange", side_effect=[0, 2600]), \
             mock.patch("game_engine.random.choice", return_value=5):
            self.assertTrue(self.engine._ensure_today_arrival_plan())

        entry = self.engine.state.today_arrival_plan[0]
        plan = entry["observation_plan"]
        self.assertEqual(plan["status"], "pending")
        self.assertEqual(plan["result"], "ladybug")
        self.assertGreaterEqual(plan["planned_turn"], entry["arrival_turn"])
        self.assertLessEqual(plan["planned_turn"], 5)
        self.assertFalse(any(action.get("action") == "observation" for action in entry["planned_actions"]))

    def test_station_purchase_only_affects_new_day_plan(self):
        self.engine.state.turn = 6
        self.engine.state.campsite_star = 3
        self.engine.state.balance = 800
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.engine.state.today_arrival_plan = [self._entry()]
        self.assertTrue(self.engine.purchase_growth_project("nature_observation_station")["success"])
        self.assertIsNone(self.engine.state.today_arrival_plan[0]["observation_plan"])

        self.engine.state.day += 1
        self.engine.state.turn = 1
        self.engine.state.today_arrival_plan_day = 0
        self.engine.state.today_arrival_plan = []
        with mock.patch.object(
            self.engine, "_calculate_daily_visitor_demand",
            return_value={"day_guest_count": 1, "overnight_guest_count": 0},
        ), mock.patch.object(self.engine, "_roll_arrival_turn", return_value=2), \
             mock.patch("game_engine.random.randrange", side_effect=[0, 0]), \
             mock.patch("game_engine.random.choice", return_value=2):
            self.engine._ensure_today_arrival_plan()
        self.assertIsNotNone(self.engine.state.today_arrival_plan[0]["observation_plan"])

    def test_execution_is_silent_once_and_preserves_catalog_order(self):
        self._active_npc()
        self.engine.state.discovered_insects = ["stag_beetle"]
        plan = {"planned_turn": 3, "status": "pending", "result": "ladybug"}
        self.engine.state.today_arrival_plan = [self._entry(observation_plan=plan)]

        self.engine.state.turn = 2
        self.engine._process_nature_observation_plans()
        self.assertEqual(plan["status"], "pending")
        self.assertEqual(self.engine.state.discovered_insects, ["stag_beetle"])

        self.engine.state.turn = 3
        self.engine._process_nature_observation_plans()
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(self.engine.state.discovered_insects, ["ladybug", "stag_beetle"])
        self.engine._process_nature_observation_plans()
        self.assertEqual(self.engine.state.discovered_insects, ["ladybug", "stag_beetle"])

    def test_not_found_and_unarrived_plan_do_not_light_catalog(self):
        self._active_npc()
        not_found = {"planned_turn": 2, "status": "pending", "result": "not_found"}
        skipped = {"planned_turn": 2, "status": "pending", "result": "ladybug"}
        skipped_entry = self._entry(npc_id=2, observation_plan=skipped)
        skipped_entry["arrival_status"] = "turned_away_full"
        self.engine.state.today_arrival_plan = [
            self._entry(observation_plan=not_found), skipped_entry,
        ]
        self.engine.state.turn = 2
        self.engine._process_nature_observation_plans()
        self.assertEqual(not_found["status"], "completed")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(self.engine.state.discovered_insects, [])

    def test_save_restore_normalizes_catalog_and_keeps_locked_plan(self):
        self.engine.state.nature_observation_station_built = True
        self.engine.state.discovered_insects = ["stag_beetle", "bad", "ladybug", "stag_beetle"]
        self.engine.state.today_arrival_plan = [
            self._entry(observation_plan={
                "planned_turn": 4, "status": "pending", "result": "firefly",
            })
        ]
        self.engine.state.today_arrival_plan_day = self.engine.state.day
        self.assertTrue(self.engine.save_state())

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertTrue(restored.state.nature_observation_station_built)
        self.assertEqual(restored.state.discovered_insects, ["ladybug", "stag_beetle"])
        self.assertEqual(restored.state.today_arrival_plan[0]["observation_plan"], {
            "planned_turn": 4, "status": "pending", "result": "firefly",
        })

    def test_old_snapshot_defaults_new_fields_and_filters_invalid_ids(self):
        self.assertTrue(self.engine.save_state())
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute(
                "SELECT snapshot_json FROM runtime_snapshot WHERE session_id = ?",
                (self.engine.session_id,),
            ).fetchone()[0]
            payload = json.loads(raw)
            payload["state"].pop("nature_observation_station_built")
            payload["state"].pop("discovered_insects")
            conn.execute(
                "UPDATE runtime_snapshot SET snapshot_json = ? WHERE session_id = ?",
                (json.dumps(payload, ensure_ascii=False), self.engine.session_id),
            )
            conn.commit()
        finally:
            conn.close()

        restored = CampingPlazaEngine(db_path=self.db_path)
        self.assertFalse(restored.state.nature_observation_station_built)
        self.assertEqual(restored.state.discovered_insects, [])


if __name__ == "__main__":
    unittest.main()
