from __future__ import annotations

import sqlite3


def test_target_sqlite_supports_wal_json_fts5_and_chinese_trigram(tmp_path):
    database = tmp_path / "capabilities.sqlite3"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT json_valid(?)", ('{"ok":true}',)).fetchone()[0] == 1
        connection.execute(
            "CREATE VIRTUAL TABLE messages USING fts5(text, tokenize='trigram')"
        )
        connection.execute("INSERT INTO messages(text) VALUES (?)", ("周末一起看电影",))
        result = connection.execute(
            "SELECT text FROM messages WHERE messages MATCH ?", ("一起看",)
        ).fetchone()
        assert result == ("周末一起看电影",)
    finally:
        connection.close()

