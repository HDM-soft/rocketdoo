"""`rkd instance traefik`: the script that installs Traefik on the VPS (#294).

The files it writes come from `core/traefik.py`, so the golden tests pin that
the script carries them byte for byte. The branch tests run the script for real
against fake `docker` and `ss` binaries, with a temp directory standing in for
the VPS, because the bug that matters here -- the second run aborting against
its own container -- only shows when the script is executed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from rocketdoo.core.instance import proxy
from rocketdoo.core.traefik import render_compose, render_traefik_yml
from rocketdoo.instance_cli import instance

EMAIL = "ops@example.com"
SNAPSHOTS = Path(__file__).parent / "snapshots"

_DOCKER = r"""#!/bin/sh
echo "docker $*" >> "$RKD_LOG"
case "$*" in
  "ps -a"*) [ -n "$RKD_OWN" ] && echo traefik ;;
  ps*) [ -n "$RKD_FOREIGN" ] && echo "proxy traefik:v3.0" ;;
  "network inspect traefik-public --format"*) [ -n "$RKD_ATTACHED" ] && printf 'proxy \n' ;;
  "network inspect"*) exit 1 ;;
esac
exit 0
"""
_SS = (
    "#!/bin/sh\n"
    'echo "State Recv-Q Send-Q Local Address:Port Peer"\n'
    '[ -n "$RKD_TAKEN" ] && echo "LISTEN 0 4096 0.0.0.0:$RKD_TAKEN 0.0.0.0:*"\n'
    "exit 0\n"
)
_TOOLS = ("bash", "sh", "awk", "sed", "grep", "tr", "sort", "cat", "mkdir", "touch", "chmod", "id", "head", "cut")


def _run_script(tmp_path, *, ss=True, **state):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for tool in _TOOLS:
        (stubs / tool).symlink_to(shutil.which(tool))
    for name, body in {"docker": _DOCKER, **({"ss": _SS} if ss else {})}.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    log = tmp_path / "commands.log"
    vps = tmp_path / "vps" / "traefik"
    env = {
        "PATH": str(stubs),
        "RKD_LOG": str(log),
        "RKD_OWN": "1" if state.get("own") else "",
        "RKD_FOREIGN": "1" if state.get("foreign") else "",
        "RKD_ATTACHED": "1" if state.get("attached") else "",
        "RKD_TAKEN": state.get("taken", ""),
    }
    script = proxy.install_script(str(vps), EMAIL)
    result = subprocess.run(["bash", "-s"], input=script, env=env, text=True, capture_output=True)
    return result, vps, log.read_text().splitlines() if log.exists() else []


class TestTheScript:
    def test_it_carries_the_local_production_files_byte_for_byte(self):
        script = proxy.install_script("/opt/traefik", EMAIL)

        assert render_traefik_yml("production", EMAIL) in script
        assert render_compose("production") in script

    @pytest.mark.parametrize(
        "snapshot", ["traefik_on_production__traefik_yml.txt", "traefik_on_production__traefik_compose.txt"]
    )
    def test_it_matches_the_production_snapshots(self, snapshot):
        script = proxy.install_script("/opt/traefik", "ops@example.com")

        assert (SNAPSHOTS / snapshot).read_text() in script

    def test_heredocs_are_quoted_so_nothing_expands(self):
        script = proxy.install_script("/opt/traefik", EMAIL)

        assert "<<'RKD_TRAEFIK_YML'" in script
        assert "<<'RKD_TRAEFIK_COMPOSE'" in script

    def test_the_directory_is_shell_quoted(self):
        assert "d='/opt/my dir; rm -rf x'" in proxy.install_script("/opt/my dir; rm -rf x", EMAIL)

    @pytest.mark.parametrize("email", ["", "a@b.c\nevil: 1", "a@b.c\rx", "RKD_TRAEFIK_YML"])
    def test_an_email_that_would_escape_the_heredoc_is_refused(self, email):
        with pytest.raises(ValueError):
            proxy.install_script("/opt/traefik", email)

    def test_acme_json_is_never_redirected_into(self):
        script = proxy.install_script("/opt/traefik", EMAIL)

        assert '> "$d/certs/acme.json"' not in script
        assert 'chmod 600 "$d/certs/acme.json"' in script


class TestRunningIt:
    def test_a_fresh_server_gets_files_certs_network_and_compose_up(self, tmp_path):
        result, vps, log = _run_script(tmp_path)

        assert result.returncode == 0, result.stderr
        assert (vps / "traefik.yml").read_text() == render_traefik_yml("production", EMAIL)
        assert (vps / "docker-compose.yml").read_text() == render_compose("production")
        assert oct((vps / "certs" / "acme.json").stat().st_mode & 0o777) == "0o600"
        assert "docker network create traefik-public" in log
        assert "docker compose up -d" in log
        assert "ports_checked=yes" in result.stdout
        assert "started=yes" in result.stdout

    def test_an_existing_acme_json_is_not_touched(self, tmp_path):
        vps = tmp_path / "vps" / "traefik"
        (vps / "certs").mkdir(parents=True)
        acme = vps / "certs" / "acme.json"
        acme.write_text('{"issued": true}')
        acme.chmod(0o644)
        before = acme.stat().st_mtime_ns

        result, _, _ = _run_script(tmp_path)

        assert result.returncode == 0, result.stderr
        assert acme.read_text() == '{"issued": true}'
        assert acme.stat().st_mtime_ns == before
        assert oct(acme.stat().st_mode & 0o777) == "0o600"

    def test_files_the_operator_may_have_tuned_are_kept(self, tmp_path):
        vps = tmp_path / "vps" / "traefik"
        vps.mkdir(parents=True)
        (vps / "traefik.yml").write_text("tuned\n")

        result, _, _ = _run_script(tmp_path)

        assert (vps / "traefik.yml").read_text() == "tuned\n"
        assert "kept= traefik.yml" in result.stdout
        assert "wrote= docker-compose.yml" in result.stdout

    def test_a_taken_port_aborts_before_anything_is_written(self, tmp_path):
        result, vps, log = _run_script(tmp_path, taken="443")

        assert result.returncode == 0
        assert "ports_taken=443" in result.stdout
        assert "started=no" in result.stdout
        assert not vps.exists()
        assert not any(line.startswith("docker compose") for line in log)

    def test_our_own_traefik_holding_the_ports_converges_instead_of_aborting(self, tmp_path):
        result, vps, log = _run_script(tmp_path, own=True, taken="80")

        assert result.returncode == 0, result.stderr
        assert "ports_taken" not in result.stdout
        assert "ports_checked=skipped" in result.stdout
        assert "started=yes" in result.stdout
        assert "docker compose up -d" in log

    def test_without_ss_or_netstat_the_ports_are_not_guessed(self, tmp_path):
        result, vps, _ = _run_script(tmp_path, ss=False, taken="80")

        assert "ports_checked=no" in result.stdout
        assert "ports_taken" not in result.stdout
        assert (vps / "traefik.yml").exists()

    @pytest.mark.parametrize("attached, expected", [(True, "yes"), (False, "no")])
    def test_someone_elses_traefik_is_left_alone(self, tmp_path, attached, expected):
        result, vps, log = _run_script(tmp_path, foreign=True, attached=attached, taken="80")

        assert result.returncode == 0
        assert "traefik_foreign=proxy" in result.stdout
        assert f"foreign_attached={expected}" in result.stdout
        assert not vps.exists()
        assert not any(line.startswith(("docker compose", "docker network create")) for line in log)


class _RecordingRun:
    def __init__(self, stdout="done=yes\n", returncode=0):
        self.calls = []
        self.stdout, self.returncode = stdout, returncode

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, self.returncode, stdout=self.stdout, stderr="boom")


class TestInstall:
    auth = {"method": "ssh_key", "key_path": "/k"}
    vps = {"host": "h.example.com", "user": "ubuntu", "port": 2222}

    def _install(self, monkeypatch, run):
        monkeypatch.setattr(proxy.subprocess, "run", run)
        return proxy.install(self.auth, self.vps, traefik_dir="/opt/traefik", email=EMAIL)

    def test_it_sends_the_script_over_stdin_to_bash(self, monkeypatch):
        run = _RecordingRun()
        self._install(monkeypatch, run)
        args, kwargs = run.calls[0]

        assert args[-2:] == ["ubuntu@h.example.com", "bash -s"]
        assert kwargs["input"] == proxy.install_script("/opt/traefik", EMAIL)

    def test_the_report_is_parsed(self, monkeypatch):
        out = "wrote= traefik.yml docker-compose.yml\nkept=\nports_checked=yes\nstarted=yes\ndone=yes\n"
        report = self._install(monkeypatch, _RecordingRun(out))

        assert report["wrote"] == ["traefik.yml", "docker-compose.yml"]
        assert report["kept"] == []
        assert report["started"] is True

    def test_a_probe_that_ends_early_is_an_error(self, monkeypatch):
        with pytest.raises(proxy.ProxyError, match="ended before"):
            self._install(monkeypatch, _RecordingRun("wrote=\n"))

    def test_a_failed_ssh_carries_stderr(self, monkeypatch):
        with pytest.raises(proxy.ProxyError, match="boom"):
            self._install(monkeypatch, _RecordingRun(returncode=255))


class TestTheCommand:
    def _project(self, tmp_path, monkeypatch, **env):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".rkd").mkdir()
        config = {
            "type": "docker",
            "vps": {"host": "prod.example.com", "port": 22, "user": "ubuntu", "auth_method": "ssh_key"},
            "domain": "erp.example.com",
            "db_user": "odoo_prod",
            **env,
        }
        (tmp_path / ".rkd" / "instance.yaml").write_text(yaml.safe_dump({"environments": {"prod": config}}))

    def _invoke(self, *args):
        return CliRunner().invoke(instance, ["traefik", "--env", "prod", *args])

    def test_a_native_environment_is_refused_with_the_reason(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch, type="native")

        result = self._invoke("--email", EMAIL)

        assert "native" in result.output
        assert "#211" in result.output

    def test_without_an_email_it_stops_before_connecting(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch)
        monkeypatch.setattr(proxy.subprocess, "run", lambda *a, **k: pytest.fail("connected"))

        assert "No Let's Encrypt email" in self._invoke().output

    def test_the_email_of_the_environment_and_a_custom_directory_reach_the_install(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch, traefik_email=EMAIL)
        run = _RecordingRun("wrote= traefik.yml\nkept=\nports_checked=yes\nstarted=yes\ndone=yes\n")
        monkeypatch.setattr(proxy.subprocess, "run", run)

        result = self._invoke("--traefik-dir", "/srv/proxy")

        assert "Traefik is running" in result.output
        assert "d=/srv/proxy" in run.calls[0][1]["input"]
        assert f"email: {EMAIL}" in run.calls[0][1]["input"]

    def test_a_refusal_exits_non_zero(self, tmp_path, monkeypatch):
        """The command is meant to be chained before a deploy, and `rkd
        instance init` next door already exits 1 on its own refusals."""
        self._project(tmp_path, monkeypatch, type="native")

        assert self._invoke("--email", EMAIL).exit_code == 1

    def test_a_success_exits_zero(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch, traefik_email=EMAIL)
        monkeypatch.setattr(
            proxy.subprocess,
            "run",
            _RecordingRun("wrote= traefik.yml\nkept=\nports_checked=yes\nstarted=yes\ndone=yes\n"),
        )

        assert self._invoke().exit_code == 0

    def test_a_taken_port_exits_non_zero(self, tmp_path, monkeypatch):
        self._project(tmp_path, monkeypatch, traefik_email=EMAIL)
        monkeypatch.setattr(
            proxy.subprocess,
            "run",
            _RecordingRun("ports_taken=80\nports_checked=yes\nstarted=no\ndone=yes\n"),
        )

        result = self._invoke()

        assert result.exit_code == 1
        assert "already taken" in result.output

    def test_a_foreign_traefik_off_the_network_exits_non_zero(self, tmp_path, monkeypatch):
        """Nothing routes this Odoo, so the next step cannot succeed either."""
        self._project(tmp_path, monkeypatch, traefik_email=EMAIL)
        monkeypatch.setattr(
            proxy.subprocess,
            "run",
            _RecordingRun("traefik_foreign=edge\nforeign_attached=no\nstarted=no\ndone=yes\n"),
        )

        result = self._invoke()

        assert result.exit_code == 1
        assert "is [bold]not[/bold] attached" in result.output or "not" in result.output

    def test_a_foreign_traefik_on_the_network_exits_zero(self, tmp_path, monkeypatch):
        """Something is serving the network already; rkd just does not own it."""
        self._project(tmp_path, monkeypatch, traefik_email=EMAIL)
        monkeypatch.setattr(
            proxy.subprocess,
            "run",
            _RecordingRun("traefik_foreign=edge\nforeign_attached=yes\nstarted=no\ndone=yes\n"),
        )

        assert self._invoke().exit_code == 0
