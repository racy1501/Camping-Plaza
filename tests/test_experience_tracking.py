import inspect
import json
import unittest
from dataclasses import asdict
from unittest.mock import patch

from camping_plaza.game_engine import CampingPlazaEngine, NPCGroup


class ExperienceTrackingTests(unittest.TestCase):
    def setUp(self):
        self.engine = CampingPlazaEngine(":memory:")
        self.npc = NPCGroup(id=1, group_size=2, visit_type="day")

    def test_positive_delta_updates_total_and_positive_ledger(self):
        self.assertEqual(self.engine.apply_satisfaction_delta(self.npc, 4), 4)
        self.assertEqual(self.npc.total_satisfaction, 64)
        self.assertEqual(self.npc.positive_experience_total, 4)
        self.assertEqual(self.npc.negative_experience_total, 0)

    def test_negative_delta_updates_total_and_negative_ledger(self):
        self.assertEqual(self.engine.apply_satisfaction_delta(self.npc, -7), -7)
        self.assertEqual(self.npc.total_satisfaction, 53)
        self.assertEqual(self.npc.positive_experience_total, 0)
        self.assertEqual(self.npc.negative_experience_total, 7)

    def test_zero_delta_does_not_change_ledger(self):
        self.npc.positive_experience_total = 3
        self.npc.negative_experience_total = 2
        self.assertEqual(self.engine.apply_satisfaction_delta(self.npc, 0), 0)
        self.assertEqual((self.npc.total_satisfaction, self.npc.positive_experience_total,
                          self.npc.negative_experience_total), (60, 3, 2))

    def test_multiple_deltas_and_repair_preserve_experience_history(self):
        for delta in (4, -10, -2, 6, 10):
            self.engine.apply_satisfaction_delta(self.npc, delta)
        self.assertEqual(self.npc.total_satisfaction, 68)
        self.assertEqual(self.npc.positive_experience_total, 20)
        self.assertEqual(self.npc.negative_experience_total, 12)

        repaired = NPCGroup(id=2, group_size=2, visit_type="overnight")
        self.engine.apply_satisfaction_delta(repaired, -10)
        self.engine.apply_satisfaction_delta(repaired, 10)
        self.assertEqual(repaired.total_satisfaction, 60)
        self.assertEqual(repaired.positive_experience_total, 10)
        self.assertEqual(repaired.negative_experience_total, 10)

    def test_old_snapshot_without_experience_fields_loads_with_zero_ledger(self):
        self.engine.npc_pool = [NPCGroup(id=9, group_size=1, visit_type="day")]
        payload = {
            "snapshot_version": self.engine.SNAPSHOT_VERSION,
            "state": asdict(self.engine.state),
            "tents": {str(key): asdict(value) for key, value in self.engine.tents.items()},
            "facilities": {key: asdict(value) for key, value in self.engine.facilities.items()},
            "npc_pool": [asdict(self.engine.npc_pool[0])],
            "npc_id_counter": 9,
        }
        payload["npc_pool"][0].pop("positive_experience_total")
        payload["npc_pool"][0].pop("negative_experience_total")

        class SnapshotConnection:
            def execute(self, *_args):
                return self

            def fetchone(self):
                return (json.dumps(payload),)

            def close(self):
                pass

        with patch("camping_plaza.game_engine.os.path.exists", return_value=True), patch(
            "camping_plaza.game_engine.sqlite3.connect", return_value=SnapshotConnection()
        ):
            self.assertEqual(self.engine.load_state(), "loaded")
        npc = self.engine.npc_pool[0]
        self.assertEqual(npc.positive_experience_total, 0)
        self.assertEqual(npc.negative_experience_total, 0)

    def test_production_satisfaction_mutations_use_the_unified_helper(self):
        source = inspect.getsource(CampingPlazaEngine)
        direct_assignments = [
            line.strip() for line in source.splitlines()
            if "total_satisfaction =" in line
        ]
        self.assertEqual(direct_assignments, ["npc.total_satisfaction = after"])

    def test_review_score_applies_negative_experience_without_mutating_total(self):
        self.npc.total_satisfaction = 80
        self.npc.negative_experience_total = 10
        self.assertEqual(self.engine._get_review_score(self.npc), 70)
        self.assertEqual(self.npc.total_satisfaction, 80)

    def test_current_rating_uses_latest_twenty_reviews_without_deleting_history(self):
        self.engine.state.review_history = [{"rating": 1} for _ in range(5)] + [{"rating": 5} for _ in range(20)]
        self.assertEqual(self.engine.get_average_rating(), 5.0)
        self.assertEqual(len(self.engine.state.review_history), 25)
        self.engine.state.review_history = [{"rating": 4} for _ in range(19)]
        self.assertEqual(self.engine.get_average_rating(), 4.0)
        self.assertEqual(self.engine.TEMPORARY_CONFLICT_EVENT_PROBABILITY, 0.25)


if __name__ == "__main__":
    unittest.main()
