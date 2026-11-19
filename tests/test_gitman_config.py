"""Tests for core/gitman_config.py's addons_path merge.

The gitman paths are appended to whatever addons_path already lists. `rkd
gitman init` runs this over a project's live config/odoo.conf, so the option
it edits may be a value spread over several indented lines (#267).
"""

import configparser

from rocketdoo.core.gitman_config import (
    gitman_has_scripts,
    gitman_module_paths,
    gitman_repos,
    gitman_ssh_hosts,
    update_odoo_conf_with_gitman,
)

SOURCES = [{"name": "odoo-argentina"}, {"name": "account-payment"}]
EXTERNAL = "/usr/lib/python3/dist-packages/odoo/external_addons"


def _entries(conf):
    parser = configparser.ConfigParser()
    parser.read(conf)
    return [path.strip() for path in parser["options"]["addons_path"].split(",")]


def test_appends_the_gitman_paths(tmp_path):
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\naddons_path = /mnt/extra-addons\n")

    update_odoo_conf_with_gitman(conf, SOURCES)

    assert _entries(conf) == ["/mnt/extra-addons", f"{EXTERNAL}/odoo-argentina", f"{EXTERNAL}/account-payment"]


def test_does_not_duplicate_a_path_already_listed(tmp_path):
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\naddons_path = /mnt/extra-addons\n")

    update_odoo_conf_with_gitman(conf, SOURCES)
    update_odoo_conf_with_gitman(conf, SOURCES)

    assert _entries(conf) == ["/mnt/extra-addons", f"{EXTERNAL}/odoo-argentina", f"{EXTERNAL}/account-payment"]


def test_a_multi_line_addons_path_keeps_every_entry(tmp_path):
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\naddons_path = /mnt/a,\n              /mnt/b\ndb_host = db\n")

    update_odoo_conf_with_gitman(conf, SOURCES)

    assert _entries(conf) == ["/mnt/a", "/mnt/b", f"{EXTERNAL}/odoo-argentina", f"{EXTERNAL}/account-payment"]
    assert "db_host = db" in conf.read_text()


def test_another_option_sharing_the_prefix_is_not_the_addons_path(tmp_path):
    """`addons_path_backup` used to match, and got the gitman paths appended."""
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\naddons_path_backup = /mnt/old\naddons_path = /mnt/extra-addons\n")

    update_odoo_conf_with_gitman(conf, SOURCES)

    assert "addons_path_backup = /mnt/old\n" in conf.read_text()
    assert _entries(conf) == ["/mnt/extra-addons", f"{EXTERNAL}/odoo-argentina", f"{EXTERNAL}/account-payment"]


def test_adds_the_option_when_absent(tmp_path):
    conf = tmp_path / "odoo.conf"
    conf.write_text("[options]\ndb_host = db\n")

    update_odoo_conf_with_gitman(conf, SOURCES)

    assert _entries(conf) == [f"{EXTERNAL}/odoo-argentina", f"{EXTERNAL}/account-payment"]


def test_keeps_crlf_line_endings(tmp_path):
    conf = tmp_path / "odoo.conf"
    conf.write_bytes(b"[options]\r\naddons_path = /mnt/a\r\ndb_host = db\r\n")

    update_odoo_conf_with_gitman(conf, [{"name": "odoo-argentina"}])

    assert conf.read_bytes() == (
        b"[options]\r\n"
        b"addons_path = /mnt/a,/usr/lib/python3/dist-packages/odoo/external_addons/odoo-argentina\r\n"
        b"db_host = db\r\n"
    )


def _gitman(tmp_path, text):
    path = tmp_path / "gitman.yml"
    path.write_text(text)
    return path


def test_module_paths_use_the_declared_location_and_derive_a_missing_name(tmp_path):
    path = _gitman(
        tmp_path,
        "location: external_addons\nsources:\n"
        "  - repo: https://github.com/a/one.git\n    name: first\n"
        "  - repo: https://github.com/OCA/web.git\n",
    )
    assert gitman_module_paths(path, "/opt/g") == ["/opt/g/external_addons/first", "/opt/g/external_addons/web"]


def test_module_paths_fall_back_to_the_gitman_1_4_default_location(tmp_path):
    path = _gitman(tmp_path, "sources:\n  - repo: https://github.com/a/one.git\n    name: first\n")
    assert gitman_module_paths(path, "/opt/g") == ["/opt/g/gitman_sources/first"]


def test_module_paths_drop_a_name_that_would_break_the_comma_separated_line(tmp_path):
    path = _gitman(
        tmp_path,
        'sources:\n  - repo: x\n    name: "a,b"\n  - repo: x\n    name: "a\\nb"\n  - repo: https://h/ok.git\n',
    )
    assert gitman_module_paths(path, "/opt/g") == ["/opt/g/gitman_sources/ok"]


def test_an_absent_broken_or_sourceless_file_gives_nothing(tmp_path):
    assert gitman_module_paths(tmp_path / "nope.yml", "/opt/g") == []
    assert gitman_module_paths(_gitman(tmp_path, "a: [unclosed"), "/opt/g") == []
    assert gitman_module_paths(_gitman(tmp_path, "location: x\n"), "/opt/g") == []
    assert gitman_module_paths(_gitman(tmp_path, "- just\n- a list\n"), "/opt/g") == []


def test_repos_lists_every_declared_url(tmp_path):
    path = _gitman(tmp_path, "sources:\n  - repo: https://h/a.git\n  - name: no-repo\n")
    assert gitman_repos(path) == ["https://h/a.git"]


def test_a_name_is_inferred_as_gitman_does_before_the_first_dot(tmp_path):
    path = _gitman(tmp_path, "sources:\n  - repo: https://h/org/addons.v2.git\n")
    assert gitman_module_paths(path, "/opt/g") == ["/opt/g/gitman_sources/addons"]


def test_a_latin_1_file_gives_nothing_instead_of_raising(tmp_path):
    path = tmp_path / "gitman.yml"
    path.write_bytes("# configuraci\xf3n\nsources: []\n".encode("latin-1"))
    assert gitman_module_paths(path, "/opt/g") == []


def test_an_absolute_or_dotted_location_does_not_produce_a_false_path(tmp_path):
    src = "sources:\n  - repo: https://h/a.git\n    name: a\n"
    assert gitman_module_paths(_gitman(tmp_path, "location: /mnt/oca\n" + src), "/opt/g") == ["/mnt/oca/a"]
    assert gitman_module_paths(_gitman(tmp_path, "location: ./x\n" + src), "/opt/g") == ["/opt/g/x/a"]
    assert gitman_module_paths(_gitman(tmp_path, "location: ''\n" + src), "/opt/g") == ["/opt/g/gitman_sources/a"]


def test_scripts_and_ssh_hosts_are_read_from_the_sources(tmp_path):
    path = _gitman(
        tmp_path,
        "sources:\n  - repo: git@gitlab.com:g/a.git\n    scripts: [sh x]\n"
        "  - repo: ssh://git@git.corp.io:2222/g/b.git\n  - repo: https://github.com/o/c.git\n",
    )
    assert gitman_has_scripts(path) is True
    assert gitman_ssh_hosts(path) == ["gitlab.com", "git.corp.io"]
    assert gitman_has_scripts(_gitman(tmp_path, "sources:\n  - repo: https://h/a.git\n")) is False
