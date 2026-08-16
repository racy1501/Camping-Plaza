"""玩家地图标记沿用现有玩家事件锚点的定向测试。"""

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OVERVIEW = _ROOT / "camping_plaza" / "frontend" / "scripts" / "overview.js"
_STYLES = _ROOT / "camping_plaza" / "frontend" / "styles" / "main.css"


class PlayerMapMovementFrontendTests(unittest.TestCase):
    def test_replayed_event_anchor_is_kept_when_state_rerenders(self):
        source = _OVERVIEW.read_text(encoding="utf-8")
        self.assertIn("let playerAnchorId = 'entrance';", source)
        self.assertIn("playerAnchorId = anchorId;", source)
        self.assertIn("const anchor = ANCHORS[playerAnchorId] || ANCHORS.entrance;", source)
        self.assertIn("showPlayerMarker(state.player_name);", source)
        self.assertNotIn("style.left = ANCHORS.entrance.left + '%'", source)
        self.assertNotIn("style.top = ANCHORS.entrance.top + '%'", source)

    def test_marker_transition_is_short_and_npc_layer_is_unchanged(self):
        styles = _STYLES.read_text(encoding="utf-8")
        self.assertIn("transition: left 0.3s ease-out, top 0.3s ease-out", styles)
        self.assertIn(".npc-layer", styles)


if __name__ == "__main__":
    unittest.main()
