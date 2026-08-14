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
        self.snapshot_json_by_session = {}
        self.statements = []


class _FakePostgresCursor:
    def __init__(self, storage):
        self.storage = storage
        self._row = None

    def execute(self, statement, parameters=None):
        self.storage.statements.append((statement, parameters))
        normalized = " ".join(statement.split()).upper()
        if "INFORMATION_SCHEMA.COLUMNS" in normalized:
            self._row = (1,)
        elif normalized.startswith("SELECT SNAPSHOT_JSON"):
            snapshot_json = self.storage.snapshot_json_by_session.get(parameters[0])
            self._row = (
                (snapshot_json,)
                if snapshot_json is not None
                else None
            )
        elif normalized.startswith("INSERT INTO RUNTIME_SNAPSHOT"):
            self.storage.snapshot_json_by_session[parameters[0]] = parameters[1]

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
            engine = CampingPlazaEngine(database_url=database_url, session_id="sess_" + "a" * 32)
            self.assertTrue(engine.use_postgres)
            engine.state.balance = 4321
            self.assertTrue(engine.save_state())
            restored = CampingPlazaEngine(database_url=database_url, session_id="sess_" + "a" * 32)

        self.assertEqual(restored.state.balance, 4321)
        sql = "\n".join(statement for statement, _ in storage.statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS runtime_snapshot", sql)
        self.assertIn("VALUES (%s, %s, NOW())", sql)
        self.assertIn("WHERE session_id = %s", sql)

    def test_postgres_sessions_do_not_overwrite_each_other(self):
        storage = _FakePostgresStorage()
        driver = mock.Mock()
        driver.connect.side_effect = lambda _url: _FakePostgresConnection(storage)
        database_url = "postgresql://user:password@example.test/camping_plaza"
        session_a = "sess_" + "a" * 32
        session_b = "sess_" + "b" * 32

        with mock.patch.object(game_engine, "psycopg2", driver):
            engine_a = CampingPlazaEngine(database_url=database_url, session_id=session_a)
            engine_b = CampingPlazaEngine(database_url=database_url, session_id=session_b)
            engine_a.state.balance = 1111
            engine_b.state.balance = 2222
            self.assertTrue(engine_a.save_state())
            self.assertTrue(engine_b.save_state())
            restored_a = CampingPlazaEngine(
                database_url=database_url, session_id=session_a, create_new=False
            )
            restored_b = CampingPlazaEngine(
                database_url=database_url, session_id=session_b, create_new=False
            )

        self.assertEqual(restored_a.state.balance, 1111)
        self.assertEqual(restored_b.state.balance, 2222)
        self.assertEqual(set(storage.snapshot_json_by_session), {session_a, session_b})

    def test_empty_database_url_keeps_sqlite_mode(self):
        engine = CampingPlazaEngine(db_path=":memory:", database_url="")
        self.assertFalse(engine.use_postgres)


if __name__ == "__main__":
    unittest.main()
