from app.api.nudges import _NUDGE_INSERT_SQL


def test_nudges_insert_sql_placeholders() -> None:
    sql = _NUDGE_INSERT_SQL
    assert "%(" not in sql
    assert ":uid" in sql
    assert ":tg" in sql
    assert ":kind" in sql
    assert ":payload" in sql
    assert "CAST(:payload AS jsonb)" in sql
