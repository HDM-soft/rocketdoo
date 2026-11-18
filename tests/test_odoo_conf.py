"""The addons_path option of an odoo.conf is read and written whole (#267).

configparser is the reference: whatever Odoo sees as the value of addons_path
is what read_addons_path() must return, and what write_addons_path() must
replace. The cross-check against configparser is a test of its own, so a
future scanner change cannot quietly drift away from what Odoo does.
"""

import configparser

import pytest

from rocketdoo.core.odoo_conf import read_addons_path, write_addons_path

SINGLE = """[options]
addons_path = /odoo/enterprise,/odoo/extra-addons
data_dir = /var/lib/odoo
"""

MULTI = """[options]
addons_path = /odoo/enterprise,
              /odoo/extra-addons,
              /odoo/external_addons/odoo-argentina
data_dir = /var/lib/odoo
"""

# What a line-only rewrite leaves behind: the first line lost its trailing
# comma and the continuation it belonged to is now glued to it by a newline.
BROKEN = """[options]
addons_path = /odoo/enterprise,/odoo/extra-addons
              /odoo/extra-addons,
              /odoo/external_addons/odoo-argentina
data_dir = /var/lib/odoo
"""


def write_conf(tmp_path, text):
    conf = tmp_path / "odoo.conf"
    conf.write_text(text)
    return conf


def odoo_sees(conf):
    """The addons_path entries Odoo ends up with.

    tools/config.py builds a RawConfigParser with no arguments, so that is what
    this mirrors: no interpolation, and both '=' and ':' open an option.
    """
    parser = configparser.RawConfigParser()
    parser.read(conf)
    return [path.strip() for path in parser["options"]["addons_path"].split(",")]


class TestRead:
    def test_single_line(self, tmp_path):
        conf = write_conf(tmp_path, SINGLE)
        assert read_addons_path(conf) == ["/odoo/enterprise", "/odoo/extra-addons"]

    def test_continuation_lines_are_part_of_the_value(self, tmp_path):
        conf = write_conf(tmp_path, MULTI)
        assert read_addons_path(conf) == [
            "/odoo/enterprise",
            "/odoo/extra-addons",
            "/odoo/external_addons/odoo-argentina",
        ]

    def test_value_starting_on_the_next_line(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path =\n    /odoo/a,\n    /odoo/b\ndata_dir = /x\n")
        assert read_addons_path(conf) == ["/odoo/a", "/odoo/b"]

    def test_tab_indented_continuation(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n\t/odoo/b\n")
        assert read_addons_path(conf) == ["/odoo/a", "/odoo/b"]

    def test_blank_line_inside_the_value(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n\n    /odoo/b\ndata_dir = /x\n")
        assert read_addons_path(conf) == ["/odoo/a", "/odoo/b"]

    def test_entry_broken_by_a_newline_is_split_back_apart(self, tmp_path):
        conf = write_conf(tmp_path, BROKEN)
        # configparser hands Odoo an entry with a newline in it, which is no
        # directory at all; reading it back as two paths is what lets the next
        # write repair the file.
        assert any("\n" in path for path in odoo_sees(conf))
        assert read_addons_path(conf) == [
            "/odoo/enterprise",
            "/odoo/extra-addons",
            "/odoo/extra-addons",
            "/odoo/external_addons/odoo-argentina",
        ]

    def test_option_absent(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\ndata_dir = /var/lib/odoo\n")
        assert read_addons_path(conf) == []

    def test_another_option_sharing_the_prefix_is_not_it(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path_backup = /odoo/old\ndata_dir = /x\n")
        assert read_addons_path(conf) == []

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(SINGLE, id="single-line"),
            pytest.param(MULTI, id="continuation-lines"),
            pytest.param("[options]\naddons_path =\n    /odoo/a,\n    /odoo/b\nx = 1\n", id="value-on-next-line"),
            pytest.param("[options]\naddons_path = /odoo/a,\n\t/odoo/b\n", id="tab-indented"),
            pytest.param("[options]\naddons_path = /odoo/a,\n\n    /odoo/b\nx = 1\n", id="blank-line-inside"),
            pytest.param("[options]\naddons_path = /odoo/a,\n    ; note\n    /odoo/b\n", id="indented-comment"),
            pytest.param("[options]\naddons_path = /odoo/a,\n; note\n    /odoo/b\n", id="comment-at-column-0"),
            pytest.param("[options]\naddons_path = /odoo/a,\n# note\n    /odoo/b\n", id="hash-comment"),
            pytest.param("[options]\naddons_path: /odoo/a,\n    /odoo/b\n", id="colon-delimiter"),
            pytest.param("[options]\naddons_path = /odoo/a,\n    /opt/x=y/addons\n", id="delimiter-inside-a-path"),
            pytest.param("[options]\naddons_path = /odoo/a\nx = 1\n", id="followed-by-another-option"),
            pytest.param("[options]\nx = 1\naddons_path = /odoo/a\n", id="last-option-in-the-file"),
            pytest.param("[options]\naddons_path = /odoo/a", id="no-trailing-newline"),
        ],
    )
    def test_matches_what_configparser_gives_odoo(self, tmp_path, text):
        """The paths read back are the ones Odoo ends up with, form by form.

        Parametrised over every shape where the two must agree, not only the
        two where agreeing is trivial: this is the guard against the scanner
        drifting away from the parser Odoo actually runs.
        """
        conf = write_conf(tmp_path, text)
        assert read_addons_path(conf) == odoo_sees(conf)

    def test_a_mis_indented_option_is_the_one_deliberate_divergence(self, tmp_path):
        """configparser swallows it as a path; we refuse to call it one.

        Odoo has already lost that option either way. Reading it back as a path
        would write "db_host = db" into the addons_path as if it were a
        directory, and dropping the block would delete the user's line.
        """
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n    db_host = db\nx = 1\n")

        assert odoo_sees(conf) == ["/odoo/a", "db_host = db"]
        assert read_addons_path(conf) == ["/odoo/a"]


class TestWrite:
    def test_replaces_the_whole_option_with_one_line(self, tmp_path):
        conf = write_conf(tmp_path, MULTI)
        assert write_addons_path(conf, ["/odoo/a", "/odoo/b"]) is True
        assert conf.read_text() == "[options]\naddons_path = /odoo/a,/odoo/b\ndata_dir = /var/lib/odoo\n"

    def test_leaves_every_other_line_alone(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\n; a note\naddons_path = /odoo/a\n; another\ndata_dir = /x\n")
        write_addons_path(conf, ["/odoo/a", "/odoo/b"])
        assert conf.read_text() == "[options]\n; a note\naddons_path = /odoo/a,/odoo/b\n; another\ndata_dir = /x\n"

    def test_returns_false_when_the_file_already_says_this(self, tmp_path):
        conf = write_conf(tmp_path, SINGLE)
        before = conf.read_text()
        assert write_addons_path(conf, ["/odoo/enterprise", "/odoo/extra-addons"]) is False
        assert conf.read_text() == before

    def test_appends_the_option_when_absent(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\ndata_dir = /x\n")
        assert write_addons_path(conf, ["/odoo/a"]) is True
        assert conf.read_text() == "[options]\ndata_dir = /x\naddons_path = /odoo/a\n"

    def test_keeps_crlf_line_endings(self, tmp_path):
        conf = tmp_path / "odoo.conf"
        conf.write_bytes(b"[options]\r\naddons_path = /odoo/a\r\ndata_dir = /x\r\n")
        write_addons_path(conf, ["/odoo/a", "/odoo/b"])
        assert conf.read_bytes() == b"[options]\r\naddons_path = /odoo/a,/odoo/b\r\ndata_dir = /x\r\n"

    def test_keeps_a_file_that_does_not_end_in_a_newline(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a")
        write_addons_path(conf, ["/odoo/b"])
        assert conf.read_text() == "[options]\naddons_path = /odoo/b"

    def test_repairs_a_broken_value(self, tmp_path):
        conf = write_conf(tmp_path, BROKEN)
        write_addons_path(conf, read_addons_path(conf))
        assert not any("\n" in path for path in odoo_sees(conf))

    def test_a_mis_indented_option_is_not_deleted(self, tmp_path):
        # configparser reads this db_host as part of addons_path, so Odoo has
        # already lost it -- but it is the user's line and must stay on disk.
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n    db_host = db\ndata_dir = /x\n")
        write_addons_path(conf, ["/odoo/b"])
        assert "db_host = db" in conf.read_text()

    def test_a_comment_inside_the_value_is_no_path(self, tmp_path):
        # configparser drops a whole-line comment wherever it is, so reading it
        # back as a path would write "; a note" into the addons_path as if it
        # were a directory.
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n    ; a note\n    /odoo/b\ndata_dir = /x\n")

        assert read_addons_path(conf) == odoo_sees(conf) == ["/odoo/a", "/odoo/b"]

    def test_a_comment_inside_the_value_survives_the_write(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n    ; a note\n    /odoo/b\ndata_dir = /x\n")

        assert write_addons_path(conf, ["/odoo/a", "/odoo/b"]) is True
        assert conf.read_text() == "[options]\naddons_path = /odoo/a,/odoo/b\n    ; a note\ndata_dir = /x\n"
        assert odoo_sees(conf) == ["/odoo/a", "/odoo/b"]
        # Second pass has nothing left to do: the comment does not make the
        # block differ from what it would write again.
        assert write_addons_path(conf, ["/odoo/a", "/odoo/b"]) is False

    def test_an_option_written_with_a_colon_is_replaced_not_duplicated(self, tmp_path):
        """':' opens an option too, so missing it appended a second one.

        configparser raises DuplicateOptionError on that file and Odoo does not
        catch it: the project stops starting at all.
        """
        conf = write_conf(tmp_path, "[options]\naddons_path: /odoo/a\ndata_dir = /x\n")

        write_addons_path(conf, ["/odoo/a", "/odoo/b"])

        assert conf.read_text() == "[options]\naddons_path = /odoo/a,/odoo/b\ndata_dir = /x\n"
        assert odoo_sees(conf) == ["/odoo/a", "/odoo/b"]

    def test_a_comment_at_column_zero_does_not_end_the_value(self, tmp_path):
        """configparser keeps consuming the value past it, so cutting there
        orphaned the rest of the paths -- the very shape of #267."""
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n; a note\n    /odoo/b\ndata_dir = /x\n")

        write_addons_path(conf, read_addons_path(conf))

        assert odoo_sees(conf) == ["/odoo/a", "/odoo/b"]
        assert "; a note" in conf.read_text()

    def test_a_path_containing_a_delimiter_stays_a_path(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n    /opt/x=y/addons\n")

        write_addons_path(conf, read_addons_path(conf))

        assert odoo_sees(conf) == ["/odoo/a", "/opt/x=y/addons"]

    def test_a_mis_indented_option_costs_no_path(self, tmp_path):
        conf = write_conf(tmp_path, "[options]\naddons_path = /odoo/a,\n    db_host = db\n    /odoo/b\nx = 1\n")

        write_addons_path(conf, read_addons_path(conf))

        assert "db_host = db" in conf.read_text()
        assert "/odoo/b" in conf.read_text().splitlines()[1]

    def test_an_entry_is_not_written_twice(self, tmp_path):
        conf = write_conf(tmp_path, SINGLE)

        write_addons_path(conf, ["/odoo/a", "/odoo/b", "/odoo/a"])

        assert odoo_sees(conf) == ["/odoo/a", "/odoo/b"]

    def test_an_indented_addons_path_belongs_to_the_option_above(self, tmp_path):
        """It opens nothing: rewriting it would edit that other option's value."""
        conf = write_conf(tmp_path, "[options]\nfoo = bar,\n    addons_path = /odoo/x\n")

        assert read_addons_path(conf) == []
        assert write_addons_path(conf, ["/odoo/a"]) is True
        assert conf.read_text() == "[options]\nfoo = bar,\n    addons_path = /odoo/x\naddons_path = /odoo/a\n"
