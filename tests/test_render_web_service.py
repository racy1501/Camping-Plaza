import os
import sys
import unittest
from unittest import mock

from fastapi.testclient import TestClient


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_api
from game_engine import CampingPlazaEngine


class RenderWebServiceTests(unittest.TestCase):
    def setUp(self):
        self.original_db_path = game_api.DB_PATH
        self.original_engine = game_api.engine
        game_api.engine = CampingPlazaEngine(db_path=":memory:", database_url="")
        self.client = TestClient(game_api.app)

    def tearDown(self):
        game_api.engine = self.original_engine
        game_api.DB_PATH = self.original_db_path

    def test_root_static_assets_and_state_api_share_one_service(self):
        root = self.client.get("/")
        self.assertEqual(root.status_code, 200)
        self.assertIn("text/html", root.headers["content-type"])
        self.assertIn("露营", root.text)

        self.assertEqual(self.client.get("/styles/main.css").status_code, 200)
        self.assertEqual(self.client.get("/scripts/overview.js").status_code, 200)
        self.assertEqual(self.client.get("/assets/底图.png").status_code, 200)

        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200)
        self.assertIn("day", state.json())
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_configured_database_path_is_absolute(self):
        configured_path = os.path.join("persistent", "game.db")
        with mock.patch.dict(os.environ, {"CAMPING_PLAZA_DB_PATH": configured_path}):
            self.assertEqual(
                game_api._resolve_database_path(), os.path.abspath(configured_path)
            )

    def test_database_url_selects_postgres_without_creating_sqlite_directory(self):
        database_url = "postgresql://user:password@example.test/camping_plaza"
        session_id = "sess_" + "a" * 32
        game_api.engine = None
        with mock.patch.dict(os.environ, {"DATABASE_URL": database_url}):
            with mock.patch.object(game_api, "CampingPlazaEngine") as engine_class:
                instance = engine_class.return_value
                self.assertIs(game_api.get_engine(session_id), instance)

        engine_class.assert_called_once_with(
            db_path=game_api.DB_PATH,
            database_url=database_url,
            session_id=session_id,
            create_new=False,
        )


if __name__ == "__main__":
    unittest.main()
