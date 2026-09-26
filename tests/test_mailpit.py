"""Tests for the core/mailpit.py contract that test_mail_cli.py does not cover:
the public read functions added for the GUI (is_enabled/status) and the
callback equivalence required of every core/ action function (CA3).
"""

from rocketdoo.core.mailpit import MailpitError, disable, enable, is_enabled, status
from rocketdoo.core.service import ServiceError


def _scaffolded_project(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    compose = tmp_path / "docker-compose.yaml"
    compose.write_text("# rkd:mailpit\n  mailpit:\n    image: x\n# /rkd:mailpit\n")
    conf_dir = tmp_path / "config"
    conf_dir.mkdir()
    (conf_dir / "odoo.conf").write_text("[options]\n; smtp_server = localhost\n; smtp_port = 25\n; smtp_ssl = False\n")
    return tmp_path


def _patch_externals(monkeypatch, db_names=("dev",)):
    import rocketdoo.core.mailpit as mailpit

    monkeypatch.setattr(mailpit, "run_compose", lambda *a, **k: 0)
    monkeypatch.setattr(mailpit, "container_running", lambda *a, **k: False)
    monkeypatch.setattr(mailpit, "databases_result", lambda *a, **k: (list(db_names), ""))
    monkeypatch.setattr(mailpit, "enable_mailpit_server", lambda *a, **k: "")
    monkeypatch.setattr(mailpit, "disable_mailpit_server", lambda *a, **k: (1, ""))


class TestMailpitErrorIsAServiceError:
    def test_inherits_message_and_hint(self):
        exc = MailpitError("no compose", "run rkd init")
        assert isinstance(exc, ServiceError)
        assert str(exc) == "no compose"
        assert exc.hint == "run rkd init"


class TestCallbackEquivalence:
    """CA3: on_progress=None and a callback must produce the same report and files."""

    def test_enable_is_equivalent_with_and_without_a_callback(self, tmp_path, monkeypatch):
        _patch_externals(monkeypatch)
        silent_root = _scaffolded_project(tmp_path / "silent")
        loud_root = _scaffolded_project(tmp_path / "loud")

        messages = []
        silent_report = enable(silent_root)
        loud_report = enable(loud_root, on_progress=lambda message, level="info": messages.append((message, level)))

        assert silent_report == loud_report
        assert (silent_root / "docker-compose.yaml").read_text() == (loud_root / "docker-compose.yaml").read_text()
        assert (silent_root / "config" / "odoo.conf").read_text() == (loud_root / "config" / "odoo.conf").read_text()

    def test_disable_is_equivalent_with_and_without_a_callback(self, tmp_path, monkeypatch):
        _patch_externals(monkeypatch)
        silent_root = _scaffolded_project(tmp_path / "silent")
        loud_root = _scaffolded_project(tmp_path / "loud")
        enable(silent_root)
        enable(loud_root)

        silent_report = disable(silent_root)
        loud_report = disable(loud_root, on_progress=lambda message, level="info": None)

        assert silent_report == loud_report
        assert (silent_root / "docker-compose.yaml").read_text() == (loud_root / "docker-compose.yaml").read_text()


class TestIsEnabledAndStatus:
    def test_is_enabled_false_without_a_compose_file(self, tmp_path):
        assert is_enabled(tmp_path) is False

    def test_is_enabled_true_after_enable(self, tmp_path, monkeypatch):
        _patch_externals(monkeypatch)
        root = _scaffolded_project(tmp_path)
        enable(root)
        assert is_enabled(root) is True

    def test_status_reports_enabled_and_running(self, tmp_path, monkeypatch):
        _patch_externals(monkeypatch)
        root = _scaffolded_project(tmp_path)
        enable(root)
        assert status(root) == {"enabled": True, "running": False}


class TestTheDatabaseIsResolvedFromProjectRoot:
    """RF1.1 all the way down to psql.

    `enable(project_root=X)` toggles X's compose and runs compose with
    `cwd=X`, but the ir.mail_server write goes through core/odoo_db, which
    falls back to `Path.cwd()` when nobody hands it the project's compose
    data. Left unthreaded, `rkd mail off` on one project archives the record
    of whichever project the process happens to be standing in -- the exact
    bug RF4.5 removed from `_get_db_container_name`.
    """

    def _two_projects(self, tmp_path, monkeypatch):
        import rocketdoo.core.mailpit as mailpit

        target = _scaffolded_project(tmp_path / "target")
        (target / "docker-compose.yaml").write_text(
            "services:\n  db:\n    container_name: db-target\n# rkd:mailpit\n  mailpit:\n    image: x\n# /rkd:mailpit\n"
        )
        elsewhere = _scaffolded_project(tmp_path / "elsewhere")
        (elsewhere / "docker-compose.yaml").write_text("services:\n  db:\n    container_name: db-elsewhere\n")
        monkeypatch.chdir(elsewhere)

        seen = []
        _patch_externals(monkeypatch)
        monkeypatch.setattr(
            mailpit, "databases_result", lambda compose_data=None, *a: (seen.append(compose_data), (["dev"], ""))[1]
        )
        return mailpit, target, seen

    def _container_in(self, compose_data):
        return (compose_data or {}).get("services", {}).get("db", {}).get("container_name")

    def test_enable_reads_the_target_projects_compose(self, tmp_path, monkeypatch):
        mailpit, target, seen = self._two_projects(tmp_path, monkeypatch)

        mailpit.enable(target)

        assert self._container_in(seen[0]) == "db-target"

    def test_disable_reads_the_target_projects_compose(self, tmp_path, monkeypatch):
        mailpit, target, seen = self._two_projects(tmp_path, monkeypatch)
        mailpit.enable(target)
        seen.clear()

        mailpit.disable(target)

        assert self._container_in(seen[0]) == "db-target"
