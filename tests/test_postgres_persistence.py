"""PostgreSQL 快照存档的无网络 mock 测试。"""

import os
import sys
import unittest
from unittest import mock


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "camping_plaza"))

import game_engine
from game_engine import CampingPlazaEngine


class _FakePostgresStorage:
    def __init__(self):
        self.snapshot_json = None
        self.statements = []


class _FakePostgresCursor:
    def __init__(self, storage):
        self.storage = storage
        self._row = None

    def execute(self, statement, parameters=None):
        self.storage.statements.append((statement, parameters))
        normalized = " ".join(statement.split()).upper()
        if normalized.startswith("SELECT"):
            self._row = (
                (self.storage.snapshot_json,)
                if self.storage.snapshot_json is not None
                else None
            )
        elif normalized.startswith("INSERT INTO RUNTIME_SNAPSHOT"):
            self.storage.snapshot_json = parameters[0]

    def fetchone(self):
        return self._row


class _FakePostgresConnection:
    def __init__(self, storage):
        self.storage = storage
        self.committed = False
        self.closed = False

    def cursor(self):
        return _FakePostgresCursor(self.storage)

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class PostgresPersistenceTests(unittest.TestCase):
    def test_postgres_snapshot_is_created_saved_and_reloaded_without_sqlite(self):
        storage = _FakePostgresStorage()
        driver = mock.Mock()
        driver.connect.side_effect = lambda _url: _FakePostgresConnection(storage)
        database_url = "postgresql://user:password@example.test/camping_plaza"

        with mock.patch.object(game_engine, "psycopg2", driver):
            engine = CampingPlazaEngine(database_url=database_url)
            self.assertTrue(engine.use_postgres)
            engine.state.balance = 4321
            self.assertTrue(engine.save_state())
            restored = CampingPlazaEngine(database_url=database_url)

        self.assertEqual(restored.state.balance, 4321)
        sql = "\n".join(statement for statement, _ in storage.statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS runtime_snapshot", sql)
        self.assertIn("VALUES (1, %s, NOW())", sql)
        self.assertIn("WHERE id = %s", sql)

    def test_empty_database_url_keeps_sqlite_mode(self):
        engine = CampingPlazaEngine(db_path=":memory:", database_url="")
        self.assertFalse(engine.use_postgres)


if __name__ == "__main__":
    unittest.main()
