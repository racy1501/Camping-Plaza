"""前端 session 初始化来源与失效存档回退的定向静态测试。"""

import unittest
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_OVERVIEW = _ROOT / "camping_plaza" / "frontend" / "scripts" / "overview.js"


class SessionInitializationFrontendTests(unittest.TestCase):
    def test_local_storage_missing_session_is_cleared_and_recreated(self):
        source = _OVERVIEW.read_text(encoding="utf-8")

        self.assertIn("const hasExplicitUrlSession = urlSessionId !== null;", source)
        self.assertIn("const requestedSessionId = hasExplicitUrlSession ? urlSessionId : savedSessionId;", source)
        self.assertIn("response.status === 404", source)
        self.assertIn("payload.detail?.error_code === 'session_not_found'", source)
        self.assertIn("!hasExplicitUrlSession && isSessionNotFoundResponse(response, payload)", source)
        self.assertIn("window.localStorage.removeItem(SESSION_STORAGE_KEY);", source)
        self.assertIn("await createSession();", source)

    def test_explicit_url_missing_session_does_not_recreate(self):
        source = _OVERVIEW.read_text(encoding="utf-8")

        self.assertIn("const hasExplicitUrlSession = urlSessionId !== null;", source)
        self.assertIn("if (!hasExplicitUrlSession && isSessionNotFoundResponse(response, payload))", source)
        self.assertIn("throw new Error('指定存档不存在或 session_id 无效。');", source)


if __name__ == "__main__":
    unittest.main()
