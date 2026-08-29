"""Unit tests for core/gitignore_manager.py.

A Rocketdoo project keeps credentials on disk — the SSH build context, the
PostgreSQL secret, admin_passwd inside odoo.conf. These tests pin down the two
properties that keep them out of git: every sensitive entry is covered, and a
user's own .gitignore is never overwritten.
"""
import pytest

from rocketdoo.core.gitignore_manager import (
    SENSITIVE_ENTRIES,
    ensure_gitignore,
    missing_entries,
    template_content,
)

SENSITIVE = [pat for pat, _ in SENSITIVE_ENTRIES]


def _entries(path):
    return {
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


class TestTemplate:
    def test_template_is_readable(self):
        assert template_content().strip()

    @pytest.mark.parametrize("pattern", SENSITIVE)
    def test_template_covers_every_sensitive_entry(self, pattern):
        assert pattern in template_content()


class TestMissingEntries:
    def test_absent_gitignore_reports_everything(self, tmp_path):
        assert [pat for pat, _ in missing_entries(tmp_path)] == SENSITIVE

    def test_full_template_reports_nothing(self, tmp_path):
        (tmp_path / ".gitignore").write_text(template_content())
        assert missing_entries(tmp_path) == []

    def test_partial_gitignore_reports_the_rest(self, tmp_path):
        (tmp_path / ".gitignore").write_text(".ssh/\n")
        missing = [pat for pat, _ in missing_entries(tmp_path)]
        assert ".ssh/" not in missing
        assert "odoo_pg_pass" in missing

    def test_commented_entry_does_not_count_as_coverage(self, tmp_path):
        """`# odoo_pg_pass` is not a rule — git would still track the file."""
        (tmp_path / ".gitignore").write_text("# odoo_pg_pass\n")
        assert "odoo_pg_pass" in [pat for pat, _ in missing_entries(tmp_path)]

    def test_each_entry_carries_a_reason(self):
        assert all(reason.strip() for _, reason in SENSITIVE_ENTRIES)


class TestEnsureGitignore:
    def test_creates_the_file_when_absent(self, tmp_path):
        action, entries = ensure_gitignore(tmp_path)
        assert action == "created"
        assert sorted(entries) == sorted(SENSITIVE)
        assert set(SENSITIVE) <= _entries(tmp_path / ".gitignore")

    def test_reports_ok_when_already_covered(self, tmp_path):
        (tmp_path / ".gitignore").write_text(template_content())
        assert ensure_gitignore(tmp_path) == ("ok", [])

    def test_appends_without_discarding_user_rules(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("# my project\n*.log\nbuild/\n")
        action, entries = ensure_gitignore(tmp_path)

        assert action == "appended"
        content = _entries(gitignore)
        assert {"*.log", "build/"} <= content       # user rules survive
        assert set(SENSITIVE) <= content            # secrets now covered

    def test_appends_only_what_is_missing(self, tmp_path):
        (tmp_path / ".gitignore").write_text(".ssh/\nodoo_pg_pass\n")
        _, entries = ensure_gitignore(tmp_path)
        assert ".ssh/" not in entries
        assert "odoo_pg_pass" not in entries

    def test_handles_a_file_without_a_trailing_newline(self, tmp_path):
        """Appending to `build/` (no \\n) must not produce `build/.ssh/`."""
        (tmp_path / ".gitignore").write_text("build/")
        ensure_gitignore(tmp_path)
        assert "build/" in _entries(tmp_path / ".gitignore")
        assert set(SENSITIVE) <= _entries(tmp_path / ".gitignore")

    def test_is_idempotent(self, tmp_path):
        ensure_gitignore(tmp_path)
        first = (tmp_path / ".gitignore").read_text()
        assert ensure_gitignore(tmp_path)[0] == "ok"
        assert (tmp_path / ".gitignore").read_text() == first

    def test_accepts_a_string_path(self, tmp_path):
        assert ensure_gitignore(str(tmp_path))[0] == "created"
