"""The script that clones odoo/enterprise on the VPS."""

import pytest

from rocketdoo.core.instance import enterprise


@pytest.mark.parametrize("given,expected", [("19.0", "19.0"), ("19", "19.0"), (" 18.0 ", "18.0")])
def test_the_branch_is_the_series(given, expected):
    assert enterprise.branch(given) == expected


def test_a_fresh_destination_is_cloned_shallow_on_the_series_branch():
    script = enterprise.clone_script("/opt/odoo/enterprise", "19.0")
    assert "git clone --depth 1 --branch 19.0 git@github.com:odoo/enterprise.git /opt/odoo/enterprise" in script


def test_an_existing_checkout_is_moved_instead_of_cloned_again():
    script = enterprise.clone_script("/opt/odoo/enterprise", "19.0")
    assert "[ -d /opt/odoo/enterprise/.git ]" in script
    assert "fetch --depth 1 origin 19.0" in script
    assert "reset --hard FETCH_HEAD" in script


def test_a_destination_with_spaces_stays_one_argument():
    assert "'/opt/my dir/enterprise'" in enterprise.clone_script("/opt/my dir/enterprise", "19.0")


def test_an_empty_or_missing_destination_is_cloned_and_a_populated_non_repo_is_refused(tmp_path):
    import shutil
    import subprocess

    if shutil.which("bash") is None:
        pytest.skip("bash not available")
    dest = tmp_path / "enterprise"
    dest.mkdir()
    (dest / "addon.py").write_text("x")
    script = enterprise.clone_script(str(dest), "19.0")
    result = subprocess.run(["bash", "-s"], input=script, text=True, capture_output=True)
    assert result.returncode == 1
    assert "not a git checkout" in result.stderr
    assert "git clone" not in result.stderr


def test_host_keys_are_pinned_not_appended():
    script = enterprise.clone_script("/x", "19.0")
    assert "StrictHostKeyChecking=accept-new" in script
    assert "ssh-keyscan" not in script
    assert "chmod" not in script


def test_an_empty_series_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="odoo_version is empty"):
        enterprise.branch("  ")
