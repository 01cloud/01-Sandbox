import os
import sqlite3
from unittest.mock import patch


class MockCursor:
    def __init__(self, sqlite_cursor):
        self.cursor = sqlite_cursor

    def execute(self, query, params=None):
        # Translate PostgreSQL %s placeholders to SQLite ? placeholders
        query = query.replace("%s", "?")
        if params is not None:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        return row

    def fetchall(self):
        return self.cursor.fetchall()

    @property
    def rowcount(self):
        return self.cursor.rowcount


class MockConnection:
    def __init__(self, sqlite_conn):
        self.conn = sqlite_conn

    def cursor(self, cursor_factory=None):
        # Configure row_factory to Row to mimic RealDictCursor dictionary access
        self.conn.row_factory = sqlite3.Row
        return MockCursor(self.conn.cursor())

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


# Start the psycopg2 patch at module-load time so it is active during test collection/importing
db_path = "/tmp/test_apikeys.db"
if os.path.exists(db_path):
    try:
        os.remove(db_path)
    except OSError:
        pass


def mock_connect(*args, **kwargs):
    conn = sqlite3.connect(db_path)
    return MockConnection(conn)


patcher = patch("psycopg2.connect", side_effect=mock_connect)
patcher.start()
