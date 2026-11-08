"""NL2SQL 安全校验器单元测试。"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.nl2sql import ensure_limit, validate_sql


class TestValidateSql:
    def test_select_ok(self):
        ok, msg = validate_sql("SELECT id, name FROM candidates WHERE years_of_exp >= 5")
        assert ok and msg == "select"

    def test_forbidden_ddl(self):
        ok, _ = validate_sql("DROP TABLE candidates")
        assert not ok

    def test_multi_statement(self):
        ok, _ = validate_sql("SELECT 1; DROP TABLE candidates")
        assert not ok

    def test_update_without_where(self):
        ok, msg = validate_sql("UPDATE candidates SET status = '已联系'")
        assert not ok and "WHERE" in msg

    def test_update_with_where(self):
        ok, msg = validate_sql("UPDATE candidates SET status = '已联系' WHERE id = 1")
        assert ok and msg == "update"

    def test_delete_with_where(self):
        ok, _ = validate_sql("DELETE FROM candidates WHERE id = 2")
        assert ok

    def test_comment_injection(self):
        ok, _ = validate_sql("SELECT * FROM candidates -- WHERE 1=1")
        assert not ok

    def test_allowed_scope(self):
        ok, _ = validate_sql("INSERT INTO candidates (name) VALUES ('x')", allowed=("select",))
        assert not ok

    def test_empty(self):
        ok, _ = validate_sql("  ")
        assert not ok


class TestEnsureLimit:
    def test_adds_limit(self):
        assert ensure_limit("SELECT * FROM candidates").endswith("LIMIT 200")

    def test_keeps_existing_limit(self):
        sql = "SELECT * FROM candidates LIMIT 10"
        assert ensure_limit(sql) == sql

    def test_ignores_write_sql(self):
        sql = "UPDATE candidates SET status = 'x' WHERE id = 1"
        assert ensure_limit(sql) == sql

    def test_strips_semicolon(self):
        assert ensure_limit("SELECT id FROM candidates;") == "SELECT id FROM candidates LIMIT 200"

    def test_custom_limit(self):
        assert ensure_limit("SELECT id FROM candidates", limit=50).endswith("LIMIT 50")
