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

    def test_npc_badge_keeps_fixed_identity_separate_from_map_location(self):
        source = _OVERVIEW.read_text(encoding="utf-8")
        styles = _STYLES.read_text(encoding="utf-8")
        self.assertIn("renderNPCs(state.active_npcs || [], state.tents || {});", source)
        self.assertIn("function fixedBadgeNumberForNpc(npc, tents)", source)
        self.assertIn("const campsiteSlot = Number(npc.campsite_slot);", source)
        self.assertIn("Object.entries(tents || {}).find", source)
        self.assertIn("tent.occupied_by", source)
        self.assertIn("NPC_BADGE_IMAGES[npc.visit_type]", source)
        self.assertIn("assets/npc_badge_day.png", source)
        self.assertIn("assets/npc_badge_overnight.png", source)
        self.assertIn("className = 'npc-badge-number'", source)
        self.assertIn("className = 'npc-badge-size'", source)
        self.assertIn("const anchorId = anchorIdForNpc(npc.location);", source)
        self.assertIn(".npc-badge", styles)

    def test_damaged_tent_indicator_follows_tent_status(self):
        source = _OVERVIEW.read_text(encoding="utf-8")
        styles = _STYLES.read_text(encoding="utf-8")
        self.assertIn("tent.status === 'broken'", source)
        self.assertIn("indicator.className = 'tent-damaged-indicator'", source)
        self.assertIn("indicator.textContent = '⚠️'", source)
        self.assertIn("anchor.appendChild(indicator);", source)
        self.assertIn("damageIndicator.remove();", source)
        self.assertIn(".tent-damaged-indicator", styles)
        self.assertIn("position: absolute", styles)
        self.assertIn("font-size: 19px", styles)
        self.assertIn(".anchor-tent1 .tent-damaged-indicator", styles)


if __name__ == "__main__":
    unittest.main()
