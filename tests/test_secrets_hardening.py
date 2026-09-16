"""Sentinel tests for the sshpass argv/env migration (#142).

Password authentication used to travel as a literal `sshpass -p <password>`
argv element, visible to any local user via `ps aux`. These tests confirm the
password now moves through `sshpass -e` / the `SSHPASS` environment variable
instead, both at the command-builder level and at each real call site, with
no VPS or Docker daemon required.
"""

import ast
import re
import subprocess
from pathlib import Path

from rocketdoo.core.deploy import vps
from rocketdoo.core.instance import deployer_docker, deployer_native, ssh_utils

SENTINEL = "RKD-SENTINEL-PASSWORD"


class TestBuildCmdAuth:
    def test_ssh_cmd_password_method_keeps_password_out_of_cmd(self):
        auth = {"method": "password", "key_path": None, "password": SENTINEL}
        cmd, env = ssh_utils.build_ssh_cmd(auth, 22, "user", "host", "echo hi")
        assert cmd[0] == "sshpass"
        assert all(SENTINEL not in item for item in cmd)
        assert env["SSHPASS"] == SENTINEL

    def test_ssh_cmd_ssh_key_method_is_unchanged(self):
        auth = {"method": "ssh_key", "key_path": "/home/user/.ssh/id_rsa", "password": None}
        cmd, env = ssh_utils.build_ssh_cmd(auth, 22, "user", "host", "echo hi")
        assert env is None
        assert "sshpass" not in cmd
        assert cmd[0] == "ssh"

    def test_rsync_cmd_password_method_keeps_password_out_of_cmd(self):
        auth = {"method": "password", "key_path": None, "password": SENTINEL}
        cmd, env = ssh_utils.build_rsync_cmd(auth, 22, "user", "host", "/local", "/remote")
        assert cmd[0] == "sshpass"
        assert all(SENTINEL not in item for item in cmd)
        assert env["SSHPASS"] == SENTINEL

    def test_rsync_cmd_ssh_key_method_is_unchanged(self):
        auth = {"method": "ssh_key", "key_path": None, "password": None}
        cmd, env = ssh_utils.build_rsync_cmd(auth, 22, "user", "host", "/local", "/remote")
        assert env is None
        assert "sshpass" not in cmd


class _RecordingRun:
    """subprocess.run stand-in that records every call and never touches the network."""

    def __init__(self):
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")


def _password_vps_config(**overrides):
    cfg = {
        "vps": {
            "host": "vps.example.com",
            "user": "deploy",
            "port": 22,
            "auth_method": "password",
            "password": SENTINEL,
        },
        "remote_path": "/opt/odoo-stage",
    }
    cfg.update(overrides)
    return cfg


class TestDockerInstanceCallSites:
    """The 3 build_ssh_cmd/build_rsync_cmd consumers in deployer_docker.py.

    check_sshpass() is stubbed so these do not depend on sshpass being
    installed on the machine running the tests (it is not on CI runners).
    """

    def _deployer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        return deployer_docker.DockerInstanceDeployer("stage", _password_vps_config(), tmp_path)

    def test_read_remote_pg_pass_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)

        deployer._read_remote_pg_pass()

        assert len(run.calls) == 1
        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_ssh_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)

        deployer._ssh("echo hi")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_rsync_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_docker.subprocess, "run", run)

        deployer._rsync("/local/", "/remote/")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL


class TestNativeInstanceCallSites:
    """The 2 build_ssh_cmd/build_rsync_cmd consumers in deployer_native.py."""

    def _deployer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        return deployer_native.NativeInstanceDeployer("stage", _password_vps_config(), tmp_path)

    def test_sync_addons_passes_sshpass_env(self, tmp_path, monkeypatch):
        (tmp_path / "addons").mkdir()
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)

        deployer._sync_addons()

        # _sync_addons issues a `mkdir` over ssh before the rsync call itself;
        # both must keep the password out of argv and in the env.
        assert len(run.calls) == 2
        for args, kwargs in run.calls:
            assert all(SENTINEL not in item for item in args)
            assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_ssh_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)

        deployer._ssh("echo hi")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL


class TestConfigureOdooStdin:
    """_configure_odoo() (T5 / RF-6): admin_passwd travels by stdin, not argv."""

    def _deployer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        cfg = _password_vps_config(admin_passwd=SENTINEL)
        return deployer_native.NativeInstanceDeployer("stage", cfg, tmp_path)

    def test_conf_content_travels_by_stdin_not_argv(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)

        deployer._configure_odoo()

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert "admin_passwd" not in " ".join(args)
        assert kwargs["input"].startswith("[options]\n")
        assert f"admin_passwd = {SENTINEL}\n" in kwargs["input"]

    def test_conf_content_is_unchanged(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, monkeypatch)
        run = _RecordingRun()
        monkeypatch.setattr(deployer_native.subprocess, "run", run)

        deployer._configure_odoo()

        conf = run.calls[0][1]["input"]
        assert conf == (
            "[options]\n"
            "addons_path = /opt/odoo-stage\n"
            "data_dir = /var/lib/odoo\n"
            f"admin_passwd = {SENTINEL}\n"
            "db_host = localhost\n"
            "db_port = 5432\n"
            "db_user = odoo_stage\n"
            "db_password = False\n"
            "db_maxconn = 64\n"
            "log_level = info\n"
            "logfile = /var/log/odoo/odoo-server.log\n"
            "workers = 2\n"
            "gevent_port = 8072\n"
            "limit_memory_hard = 1610612736\n"
            "limit_memory_soft = 1073741824\n"
            "proxy_mode = True\n"
            "list_db = False\n"
        )


def _password_vps_deployer_config(**overrides):
    cfg = {
        "connection": {
            "host": "vps.example.com",
            "user": "deploy",
            "port": 22,
            "password": SENTINEL,
        },
        "deployment_type": "docker",
        "docker": {
            "container_name": "odoo",
            "compose_path": "/opt/odoo",
            "addons_mount": "/mnt/extra-addons",
        },
    }
    cfg.update(overrides)
    return cfg


class TestVpsDeployerCallSites:
    """The 3 sshpass call sites in core/deploy/vps.py: _run_ssh_command,
    _upload_directory, _upload_file_scp.
    """

    def _deployer(self, tmp_path):
        return vps.VPSDeployer("production", _password_vps_deployer_config(), tmp_path)

    def test_run_ssh_command_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("echo hi")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_upload_directory_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._upload_directory(tmp_path / "module", "/mnt/extra-addons/module")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_upload_file_scp_passes_sshpass_env(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._upload_file_scp(tmp_path / "module.zip", "/mnt/extra-addons/module.zip")

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert kwargs["env"]["SSHPASS"] == SENTINEL

    def test_ssh_key_method_still_has_no_sshpass_env(self, tmp_path, monkeypatch):
        cfg = _password_vps_deployer_config(connection={"host": "vps.example.com", "user": "deploy", "port": 22})
        cfg["connection"]["ssh_key"] = "~/.ssh/id_rsa"
        deployer = vps.VPSDeployer("production", cfg, tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("echo hi")

        args, kwargs = run.calls[0]
        assert "sshpass" not in args
        assert kwargs["env"] is None


class TestRunSshCommandSudoStdin:
    """_run_ssh_command(use_sudo=True) with password auth (T4 / RF-4, RF-5).

    The remote sudo password used to be interpolated into the command string
    (`echo '{password}' | sudo -S ...`), which put it in ps aux on both ends
    and let a password containing a quote inject arbitrary shell commands. It
    now travels through the stdin of subprocess.run instead.
    """

    MALICIOUS_PASSWORD = "x'; touch /tmp/pwn; '"

    def _deployer(self, tmp_path, password):
        cfg = _password_vps_deployer_config()
        cfg["connection"]["password"] = password
        return vps.VPSDeployer("production", cfg, tmp_path)

    def test_sudo_password_goes_through_stdin_not_argv(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, SENTINEL)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("systemctl restart odoo", use_sudo=True)

        args, kwargs = run.calls[0]
        assert all(SENTINEL not in item for item in args)
        assert "echo" not in args[-1]
        assert args[-1] == "sudo -S -p '' systemctl restart odoo"
        assert kwargs["input"] == SENTINEL + "\n"

    def test_malicious_password_does_not_alter_remote_command(self, tmp_path, monkeypatch):
        for password in ("trivial-pass", self.MALICIOUS_PASSWORD):
            deployer = self._deployer(tmp_path, password)
            run = _RecordingRun()
            monkeypatch.setattr(vps.subprocess, "run", run)

            deployer._run_ssh_command("systemctl restart odoo", use_sudo=True)

            args, kwargs = run.calls[0]
            assert args[-1] == "sudo -S -p '' systemctl restart odoo"
            assert kwargs["input"] == password + "\n"

    def test_sudo_without_password_auth_falls_back_to_plain_sudo(self, tmp_path, monkeypatch):
        cfg = _password_vps_deployer_config(connection={"host": "vps.example.com", "user": "deploy", "port": 22})
        cfg["connection"]["ssh_key"] = "~/.ssh/id_rsa"
        deployer = vps.VPSDeployer("production", cfg, tmp_path)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("systemctl restart odoo", use_sudo=True)

        args, kwargs = run.calls[0]
        assert args[-1] == "sudo systemctl restart odoo"
        assert kwargs["input"] is None

    def test_without_sudo_input_stays_none(self, tmp_path, monkeypatch):
        deployer = self._deployer(tmp_path, SENTINEL)
        run = _RecordingRun()
        monkeypatch.setattr(vps.subprocess, "run", run)

        deployer._run_ssh_command("echo hi")

        args, kwargs = run.calls[0]
        assert kwargs["input"] is None


class TestNoSecretInConsoleOutput:
    """RF-9(b): the sentinel must never reach the captured console output.

    This is the scenario named in the spec's audit example: someone adds a
    `console.print(f"...{self.password}...")` to a deployer. The argv/env
    assertions above would stay green (the process launch is still clean),
    so this needs its own runtime check with `capsys`.
    """

    def test_vps_deployer(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(vps.subprocess, "run", _RecordingRun())
        deployer = vps.VPSDeployer("production", _password_vps_deployer_config(), tmp_path)

        deployer._run_ssh_command("systemctl restart odoo", use_sudo=True)
        deployer._upload_directory(tmp_path / "module", "/mnt/extra-addons/module")
        deployer._upload_file_scp(tmp_path / "module.zip", "/mnt/extra-addons/module.zip")

        assert SENTINEL not in capsys.readouterr().out

    def test_docker_instance_deployer(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        monkeypatch.setattr(deployer_docker.subprocess, "run", _RecordingRun())
        deployer = deployer_docker.DockerInstanceDeployer("stage", _password_vps_config(), tmp_path)

        deployer._ssh("echo hi")
        deployer._rsync("/local/", "/remote/")
        deployer._read_remote_pg_pass()

        assert SENTINEL not in capsys.readouterr().out

    def test_native_instance_deployer(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ssh_utils, "check_sshpass", lambda: True)
        monkeypatch.setattr(deployer_native.subprocess, "run", _RecordingRun())
        cfg = _password_vps_config(admin_passwd=SENTINEL)
        deployer = deployer_native.NativeInstanceDeployer("stage", cfg, tmp_path)

        deployer._configure_odoo()
        deployer._ssh("echo hi")

        assert SENTINEL not in capsys.readouterr().out


# ─── structural guard: a new call site must be caught without a new test ───

ROCKETDOO_ROOT = Path(__file__).resolve().parent.parent / "rocketdoo"

# Matches an identifier that looks like it holds a credential: password,
# passwd, secret or token, in any case, as a substring (admin_passwd,
# self._sshpass_password and INSTANCE_STAGE_PASSWORD must all match).
_SENSITIVE_NAME_RE = re.compile(r"password|passwd|secret|token", re.IGNORECASE)

# Process-launching methods from stdlib modules. Matched by (module, method)
# rather than by method name alone: a bare-name match on "run" or "call" also
# fires on unrelated calls like `uvicorn.run(...)`, which is not a sink.
_STDLIB_SINK_MODULES = {"subprocess", "os", "asyncio"}
_STDLIB_SINK_METHODS = {
    "run",
    "Popen",
    "call",
    "check_call",
    "check_output",  # subprocess.*
    "system",
    "popen",  # os.*
    "create_subprocess_exec",
    "create_subprocess_shell",  # asyncio.*, used by gui/server.py
}

# Local helpers that build the ssh/rsync argv passed to a sink, or launch one
# directly on `self`. Their names are specific enough to match bare, unlike
# the generic stdlib methods above.
_LOCAL_SINK_NAMES = {
    "_ssh",
    "_run_ssh_command",
    "_rsync",
    "build_ssh_cmd",
    "build_rsync_cmd",
}


def _is_sink_call(node: ast.Call) -> bool:
    name = _called_name(node.func)
    if name in _LOCAL_SINK_NAMES:
        return True
    if name not in _STDLIB_SINK_METHODS or not isinstance(node.func, ast.Attribute):
        return False
    base = node.func.value
    return isinstance(base, ast.Name) and base.id in _STDLIB_SINK_MODULES


# The only two channels a secret is allowed to travel through once it is
# about to reach a sink: subprocess's own `input=`/`env=` keywords, and the
# first argument of sshpass_wrap() (which moves it into `env["SSHPASS"]`).
_SAFE_KEYWORDS = {"input", "env"}
_SELF_PROTECTING_BUILDERS = {"sshpass_wrap", "build_ssh_cmd", "build_rsync_cmd"}


def _called_name(func_node: ast.AST) -> str | None:
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def _identifier(node: ast.AST) -> str | None:
    return _called_name(node) if isinstance(node, (ast.Name, ast.Attribute)) else None


def _is_sensitive(node: ast.AST) -> bool:
    name = _identifier(node)
    return bool(name and _SENSITIVE_NAME_RE.search(name))


def _sensitive_refs(node: ast.AST) -> list[str]:
    return [
        _identifier(child) for child in ast.walk(node) if isinstance(child, (ast.Name, ast.Attribute)) and _is_sensitive(child)
    ]


def _safe_node_ids(func: ast.AST) -> set[int]:
    """Node ids that a secret is deliberately routed through and must be skipped."""
    safe: set[int] = set()
    assigns_by_name: dict[str, list[ast.AST]] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names = [target.id]
                else:
                    names = [elt.id for elt in getattr(target, "elts", []) if isinstance(elt, ast.Name)]
                for name in names:
                    assigns_by_name.setdefault(name, []).append(node.value)

    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        if _called_name(node.func) == "sshpass_wrap" and node.args:
            safe.add(id(node.args[0]))
        for kw in node.keywords:
            if kw.arg not in _SAFE_KEYWORDS or kw.value is None:
                continue
            if isinstance(kw.value, ast.Name):
                for value in assigns_by_name.get(kw.value.id, []):
                    if isinstance(value, ast.Call) and _called_name(value.func) in _SELF_PROTECTING_BUILDERS:
                        continue  # already covered by its own sshpass_wrap() call
                    safe.add(id(value))
            else:
                safe.add(id(kw.value))
    return safe


def _walk_pruned(node: ast.AST, safe_ids: set[int]):
    if id(node) in safe_ids:
        return
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_pruned(child, safe_ids)


def _violations_in_function(func: ast.AST) -> list[tuple[int, str]]:
    if not any(isinstance(n, ast.Call) and _is_sink_call(n) for n in ast.walk(func)):
        return []

    safe_ids = _safe_node_ids(func)
    violations = []
    for node in _walk_pruned(func, safe_ids):
        if isinstance(node, (ast.List, ast.Tuple)):
            for elt in node.elts:
                if isinstance(elt, (ast.Name, ast.Attribute)) and _is_sensitive(elt):
                    violations.append((elt.lineno, _identifier(elt)))
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    violations += [(value.lineno, name) for name in _sensitive_refs(value.value)]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            violations += [(node.lineno, name) for name in _sensitive_refs(node.right)]
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            # "echo '" + password + "' | sudo -S " is the T4 bug written with
            # concatenation instead of an f-string.
            violations += [(node.lineno, name) for name in _sensitive_refs(node)]
        elif isinstance(node, ast.Call) and _called_name(node.func) == "append":
            violations += [(node.lineno, name) for arg in node.args for name in _sensitive_refs(arg)]
        elif isinstance(node, ast.Call) and _called_name(node.func) == "format":
            for arg in (*node.args, *(kw.value for kw in node.keywords)):
                violations += [(node.lineno, name) for name in _sensitive_refs(arg)]
    return violations


def test_no_secret_reaches_subprocess_argv_construction():
    """Structural guard (RF-9/RF-10/CA-9): catches a *new* call site.

    Rather than re-checking the call sites already covered above, this walks
    every function in rocketdoo/ (never tests/, which hard-codes fixture
    passwords on purpose) that launches a process, directly or through the
    local ssh/rsync helpers, and fails if a credential-looking value is used
    to build the command outside of sshpass_wrap()/input=/env=.

    It is a net for the obvious shape, NOT a guarantee. It matches on
    identifier names, and only inside a function that itself calls a sink, so
    all of these get through:

      - the secret read through a subscript of a literal key
        (`conn["password"]`) or renamed first (`pw = self.password`)
      - the command built in a helper that does not launch anything itself
      - a sink reached under a name not in _STDLIB_SINK_METHODS/_LOCAL_SINK_NAMES

    Closing those needs real dataflow analysis, which is a bigger machine than
    the bug it guards. Reviewing a diff that touches a deploy path is still the
    actual control; this only makes the careless version fail loudly.

    Verified to actually fail: temporarily reverted the T4 fix in vps.py
    (`command = f"echo '{self.password}' | sudo -S {command}"`) and confirmed
    this test turned red before restoring it.
    """
    violations = []
    for path in sorted(ROCKETDOO_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for lineno, name in _violations_in_function(func):
                violations.append(f"{path.relative_to(ROCKETDOO_ROOT.parent)}:{lineno} references '{name}'")

    assert not violations, "Credential-looking value reachable from a subprocess argv:\n" + "\n".join(violations)


class TestAuthMethodDecidesSshpass:
    """RF-7: the method picks the auth, not the truthiness of a stray key.

    ssh_prefix() keyed on auth["method"]; after T2 the call sites pass a
    password straight to sshpass_wrap, so a dict carrying both a key and a
    password must still take the key path.
    """

    def test_ssh_key_ignores_a_stray_password(self):
        from rocketdoo.core.instance.ssh_utils import build_ssh_cmd

        auth = {"method": "ssh_key", "key_path": "/tmp/k", "password": "stray"}
        cmd, env = build_ssh_cmd(auth, 22, "u", "h", "echo hi")

        assert "sshpass" not in cmd
        assert not (env and "SSHPASS" in env)

    def test_password_method_uses_the_environment(self):
        from rocketdoo.core.instance.ssh_utils import build_ssh_cmd

        auth = {"method": "password", "key_path": None, "password": "s3cr3t"}
        cmd, env = build_ssh_cmd(auth, 22, "u", "h", "echo hi")

        assert cmd[:2] == ["sshpass", "-e"]
        assert not any("s3cr3t" in str(a) for a in cmd)
        assert env["SSHPASS"] == "s3cr3t"

    def test_rsync_follows_the_same_rule(self):
        from rocketdoo.core.instance.ssh_utils import build_rsync_cmd

        auth = {"method": "ssh_key", "key_path": "/tmp/k", "password": "stray"}
        cmd, env = build_rsync_cmd(auth, 22, "u", "h", "/src", "/dst")

        assert "sshpass" not in cmd
        assert not (env and "SSHPASS" in env)


class TestMultilinePasswordDoesNotLeakIntoRemoteStdin:
    """M1: sudo reads one line; the rest would land in the remote command."""

    def test_only_the_first_line_is_sent(self, tmp_path):
        from unittest.mock import patch

        from rocketdoo.core.deploy.vps import VPSDeployer

        cfg = {
            "connection": {"host": "h", "user": "u", "password": "first\nsecond"},
            "deployment_type": "docker",
        }
        deployer = VPSDeployer("prod", cfg, tmp_path)
        seen = {}

        def _run(cmd, **kw):
            seen["input"] = kw.get("input")

            class R:
                returncode = 0
                stdout = ""
                stderr = ""

            return R()

        with patch("rocketdoo.core.deploy.vps.subprocess.run", _run):
            deployer._run_ssh_command("systemctl restart odoo", use_sudo=True)

        assert seen["input"] == "first\n"
