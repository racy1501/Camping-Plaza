"""前端玩家名称应读取正式存档字段的定向静态测试。"""

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_API = _ROOT / "camping_plaza" / "game_api.py"
_INDEX = _ROOT / "camping_plaza" / "frontend" / "index.html"
_OVERVIEW = _ROOT / "camping_plaza" / "frontend" / "scripts" / "overview.js"


class PlayerNameFrontendTests(unittest.TestCase):
    def test_api_state_exposes_saved_player_name(self):
        source = _API.read_text(encoding="utf-8")
        self.assertIn('state["player_name"] = eng.state.player_name', source)

    def test_marker_reads_player_name_with_safe_placeholder(self):
        overview = _OVERVIEW.read_text(encoding="utf-8")
        index = _INDEX.read_text(encoding="utf-8")
        self.assertIn("function showPlayerMarker(playerName)", overview)
        self.assertIn("String(playerName || '玩家')", overview)
        self.assertIn("showPlayerMarker(state.player_name)", overview)
        self.assertNotIn("textContent = '小克'", overview)
        self.assertNotIn('title="小克 待命"', index)
        self.assertIn('id="playerLabel">玩家 待命</span>', index)


if __name__ == "__main__":
    unittest.main()
