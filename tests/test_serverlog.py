"""engine.serverlog: reading backend logs and giving each window only its own.

Nothing here starts a real ssh, docker or journalctl - the readers are behind a
factory precisely so a test can put its own in. The one exception is the local
file reader, which is the only transport with logic worth exercising for real
(rotation and truncation), and it runs against a tmp_path.
"""

import datetime
import json
import os
import threading
import time

import pytest

from engine import serverlog as sl


LOCAL = "localhost:8069"
DEV = "https://dev.example.com/"


def _config(**overrides):
    data = {
        "connections": [
            {"name": "here", "type": "local"},
            {"name": "dev", "type": "ssh", "host": "dev.example.com",
             "user": "deploy", "identity": "~/.ssh/id_ed25519"},
        ],
        "logs": [
            {"name": "app", "connection": "here", "env": LOCAL, "type": "file",
             "path": "/var/log/app.log", "format": "odoo", "default": True},
            {"name": "app", "connection": "dev", "env": DEV, "type": "docker",
             "container": "app", "format": "odoo", "default": True},
            {"name": "nginx", "connection": "dev", "env": DEV, "type": "file",
             "path": "/var/log/nginx/error.log", "format": "nginx"},
            {"name": "system", "connection": "dev", "env": DEV, "type": "journal",
             "unit": "app.service", "format": "syslog"},
        ],
    }
    data.update(overrides)
    return sl.parse_config(data)


def _source(**kwargs):
    kwargs.setdefault("name", "app")
    kwargs.setdefault("connection", sl.Connection("here", "local"))
    kwargs.setdefault("envs", [LOCAL])
    return sl.LogSource(**kwargs)


# --------------------------------------------------------------- config parsing
def test_a_log_naming_a_connection_that_does_not_exist_is_named_as_such():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "typo",
                                   "env": LOCAL, "path": "/x"}]})
    # The message has to say what IS defined, or the only way to fix a typo is to
    # go and read the file.
    assert "no connection named 'typo'" in str(exc.value)
    assert "here" in str(exc.value)


def test_the_same_log_name_twice_in_one_environment_is_rejected():
    # --server-log=app has to mean one log. Across environments it is fine, and
    # the fixture above relies on that.
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                                   "path": "/a"},
                                  {"name": "app", "connection": "here", "env": LOCAL,
                                   "path": "/b"}]})
    assert "unique per environment" in str(exc.value)


def test_the_same_log_name_on_two_environments_is_fine():
    config = _config()
    assert [s.name for s in config.for_env(LOCAL)] == ["app"]
    assert [s.name for s in config.for_env(DEV)] == ["app", "nginx", "system"]


def test_a_log_bound_to_no_environment_is_rejected():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "here", "path": "/x"}]})
    assert "at least one environment" in str(exc.value)


def test_an_ssh_connection_without_a_host_is_rejected():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "dev", "type": "ssh"}], "logs": []})
    assert "needs a 'host'" in str(exc.value)


def test_a_file_log_without_a_path_is_rejected():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                                   "type": "file"}]})
    assert "'path' is required" in str(exc.value)


def test_an_unknown_log_type_lists_the_known_ones():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                                   "type": "carrier-pigeon"}]})
    for known in sl.LOG_TYPES:
        assert known in str(exc.value)


def test_an_unknown_format_points_at_the_escape_hatch():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                                   "path": "/x", "format": "log4j"}]})
    assert "timestamp" in str(exc.value)     # "or give timestamp/level explicitly"


def test_unknown_keys_survive_so_a_comment_can_live_in_the_file(tmp_path):
    config = sl.parse_config({
        "connections": [{"name": "here", "type": "local", "_comment": "hi"}],
        "logs": [{"name": "app", "connection": "here", "env": LOCAL, "path": "/x",
                  "_comment": "there"}]})
    assert config.connections[0].extra["_comment"] == "hi"
    assert config.logs[0].extra["_comment"] == "there"


def test_a_missing_file_is_an_empty_config_not_an_error(tmp_path):
    # --server-log is opt-in; not having configured it is not a broken install.
    config = sl.load_config(str(tmp_path / "nope.json"))
    assert config.logs == [] and config.connections == []


def test_a_byte_order_mark_does_not_break_the_read(tmp_path):
    # Same trap users.json fell into: a Windows shell writes a BOM.
    path = tmp_path / "logsources.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"connections": [{"name": "here", "type": "local"}],
         "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                   "path": "/x"}]}).encode("utf-8"))
    assert [s.name for s in sl.load_config(str(path)).logs] == ["app"]


# -------------------------------------------------------------------- selection
def test_a_bare_flag_takes_the_logs_marked_default():
    assert [s.name for s in sl.resolve(_config(), DEV)] == ["app"]


def test_all_takes_every_log_for_that_environment():
    assert [s.name for s in sl.resolve(_config(), DEV, sl.ALL)] == \
        ["app", "nginx", "system"]


def test_none_takes_nothing():
    assert sl.resolve(_config(), DEV, sl.NONE) == []


def test_a_named_log_is_matched_within_the_environment():
    assert [s.name for s in sl.resolve(_config(), DEV, ["nginx"])] == ["nginx"]


def test_a_name_from_another_environment_says_so():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.resolve(_config(), LOCAL, ["nginx"])
    # Distinguishing this from a typo is the difference between "you meant
    # something that exists" and "check your spelling".
    assert "configured, but not for" in str(exc.value)


def test_a_name_that_exists_nowhere_is_a_typo():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.resolve(_config(), DEV, ["ngnix"])
    assert "configured, but not for" not in str(exc.value)
    assert "nginx" in str(exc.value)          # what IS available


def test_a_run_over_several_environments_gets_each_ones_logs():
    # No --env launches every environment at once, and each window wants its own
    # server's log - not a merge and not the first one's.
    chosen = sl.resolve(_config(), [LOCAL, DEV])
    assert [s.connection.name for s in chosen] == ["here", "dev"]


def test_a_name_only_has_to_match_somewhere_in_the_run():
    # nginx exists on dev and not locally. Failing the whole run for that would
    # make the flag unusable the moment --env is left off.
    assert [s.name for s in sl.resolve(_config(), [LOCAL, DEV], ["nginx"])] == ["nginx"]


# ------------------------------------------------------- command per connection
def test_a_local_file_log_needs_no_command_wrapping():
    source = _source(type="file", path="/var/log/app.log")
    assert source.connection.wrap(source.command()) == \
        ["tail", "-n", "0", "-F", "/var/log/app.log"]


@pytest.mark.parametrize("kwargs, expected", [
    (dict(type="file", path="/var/log/nginx/error.log"),
     "tail -n 0 -F /var/log/nginx/error.log"),
    (dict(type="docker", container="app"), "docker logs -n 0 -f app"),
    (dict(type="journal", unit="app.service"),
     "journalctl --no-pager -f -n 0 -u app.service"),
    (dict(type="journal", unit="*"), "journalctl --no-pager -f -n 0"),
])
def test_every_reader_runs_over_the_same_ssh_connection(kwargs, expected):
    connection = sl.Connection("dev", "ssh", host="h", user="deploy")
    argv = connection.wrap(_source(connection=connection, **kwargs).command())
    assert argv[0] == "ssh" and argv[-2] == "deploy@h"
    assert argv[-1] == expected


def test_ssh_never_prompts_and_never_trusts_an_unknown_host():
    # A reader runs on a background thread: a password prompt there hangs unseen.
    # And auto-accepting the host key would mean trusting whatever answers.
    argv = sl.Connection("dev", "ssh", host="h").wrap(["true"])
    assert "BatchMode=yes" in argv
    assert not any("StrictHostKeyChecking" in part for part in argv)


def test_an_identity_and_a_port_reach_the_ssh_command():
    connection = sl.Connection("dev", "ssh", host="h", user="u",
                               identity="~/.ssh/k", port=2222)
    argv = connection.wrap(["true"])
    assert "-p" in argv and "2222" in argv
    assert os.path.expanduser("~/.ssh/k") in argv


def test_a_reader_command_is_quoted_for_the_remote_shell():
    # The far side gets one string through a shell; a path with a space in it
    # must not arrive as two arguments.
    connection = sl.Connection("dev", "ssh", host="h")
    argv = connection.wrap(_source(type="file", path="/var/log/my app.log").command())
    assert "'/var/log/my app.log'" in argv[-1]


def test_a_local_file_log_reads_the_file_itself():
    # Rather than spawning a tail per window on the user's own machine.
    assert isinstance(sl.make_reader(_source(type="file", path="/x")),
                      sl._LocalFileReader)


def test_a_remote_file_log_shells_out():
    source = _source(connection=sl.Connection("dev", "ssh", host="h"),
                     type="file", path="/x")
    assert isinstance(sl.make_reader(source), sl._ProcessReader)


# ------------------------------------------------------------- parsing formats
def _parse(fmt, *lines, **kwargs):
    """Parse as though the reader saw each line the moment it was written.

    Which is the normal case, and the one these cases are about: they check the
    reading of a shape, not the safety net that catches a log whose clock does not
    agree with this machine's (see the drift cases at the end).
    """
    parser = sl.LineParser(_source(format=fmt))
    now = kwargs.get("now") or datetime.datetime(2026, 8, 19, 12, 0, 0)
    parsed = []
    for line in lines:
        own = parser.timestamp_of(line, now=now)
        parsed.append(parser.parse(line, now=now, seen_at=own))
    return parsed


def test_odoo_lines_give_up_their_time_and_level():
    (ts, level, text), = _parse("odoo",
                                "2026-08-19 10:53:09,123 42 ERROR db odoo.http: boom")
    assert level == "ERROR"
    assert time.strftime("%H:%M:%S", time.localtime(ts)) == "10:53:09"
    assert text.endswith("boom")


def test_nginx_error_lines_are_understood():
    (ts, level, _), = _parse("nginx", "2026/08/19 10:53:10 [error] 42#0: *1 upstream")
    assert level == "ERROR"
    assert time.strftime("%H:%M:%S", time.localtime(ts)) == "10:53:10"


def test_syslog_lines_get_the_year_they_do_not_carry():
    (ts, _, _), = _parse("syslog", "Aug 19 10:53:11 host app[42]: hello")
    assert time.strftime("%Y-%m-%d", time.localtime(ts)) == "2026-08-19"


def test_a_syslog_line_from_last_december_read_in_january_is_not_next_december():
    # strptime defaults to 1900 and the obvious fix - "use this year" - puts a
    # December line eleven months in the future every New Year.
    (ts, _, _), = _parse("syslog", "Dec 31 23:59:00 host app[42]: hello",
                         now=datetime.datetime(2026, 1, 1, 0, 1, 0))
    assert time.strftime("%Y-%m-%d", time.localtime(ts)) == "2025-12-31"


@pytest.mark.parametrize("line, hhmmss", [
    ("2026-08-19T10:53:12Z INFO up", "10:53:12"),
    ("2026-08-19T10:53:12.5Z INFO up", "10:53:12"),
    ("2026-08-19T10:53:12.123456789Z INFO up", "10:53:12"),
    ("2026-08-19 10:53:12+00:00 INFO up", "10:53:12"),
    ("2026-08-19T10:53:12+0000 INFO up", "10:53:12"),
])
def test_iso_spellings_python_39_refuses_are_still_read(line, hhmmss):
    # fromisoformat on 3.9 rejects "Z", a 1- or 9-digit fraction and "+0000".
    (ts, _, _), = _parse("iso", line)
    assert time.strftime("%H:%M:%S", time.gmtime(ts)) == hhmmss


def test_an_offset_in_the_line_beats_the_configured_timezone():
    parser = sl.LineParser(_source(format="iso",
                                   timestamp={"regex": r"^(\S+)", "format": "iso",
                                              "tz": "+05:00"}))
    line = "2026-08-19T10:00:00+00:00 hello"
    ts, _, _ = parser.parse(line, seen_at=parser.timestamp_of(line))
    assert time.strftime("%H:%M:%S", time.gmtime(ts)) == "10:00:00"


def test_a_line_with_no_timestamp_inherits_the_previous_one():
    # The whole point of pulling server logs next to a failing step: a traceback
    # whose body drifts to "now" is detached from the message that introduced it,
    # and then falls outside the scenario's window in the report.
    parsed = _parse("odoo",
                    "2026-08-19 10:53:09,123 42 ERROR db odoo.http: Traceback:",
                    "  File \"/app/http.py\", line 1",
                    "ValueError: boom")
    assert len({ts for ts, _, _ in parsed}) == 1
    assert [level for _, level, _ in parsed] == ["ERROR", "ERROR", "ERROR"]


def test_a_backends_own_level_word_lands_on_one_of_ours():
    # syslog says CRIT where Python says CRITICAL; both are the same severity.
    (_, level, _), = _parse("syslog", "Aug 19 10:53:11 h app[1]: CRIT disk gone")
    assert level == "CRITICAL"


def test_a_bad_regex_in_the_config_is_reported_against_its_log():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.LineParser(_source(name="app", timestamp={"regex": "([unclosed",
                                                     "format": "iso"}))
    assert "app" in str(exc.value)


# ------------------------------------------------------------------- the hub
class _FakeReader(sl._Reader):
    """Yields whatever the test appends to ``feed``, then idles."""

    made = []

    def __init__(self, source):
        sl._Reader.__init__(self, source)
        self.feed = []
        _FakeReader.made.append(self)

    def lines(self, stop):
        index = 0
        while not stop.is_set():
            if index < len(self.feed):
                yield self.feed[index]
                index += 1
            else:
                stop.wait(0.01)


@pytest.fixture
def hub_factory():
    """A hub whose readers are fakes, not started - the test pumps it by hand."""
    made = []

    def build(sources, **kwargs):
        kwargs.setdefault("on_lines", lambda *a: None)
        hub = sl.ServerLogHub(sources, reader_factory=_FakeReader, **kwargs)
        made.append(hub)
        return hub

    yield build
    for hub in made:
        hub.close()


def _feed(hub, source_name, *lines):
    """Push raw lines through the stream for ``source_name``, as its reader would."""
    for stream in hub._streams:
        if stream.source.name == source_name:
            for line in lines:
                hub._ingest(stream, line)
            return
    raise AssertionError("no stream named %r" % source_name)


def _stamp(offset_s, base=None):
    moment = datetime.datetime.fromtimestamp((base or time.time()) + offset_s)
    return moment.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]


def test_a_session_sees_only_what_was_written_after_its_window_opened(hub_factory):
    batches = []
    source = _source(format="odoo")
    hub = hub_factory([source], on_lines=lambda s, l, lines: batches.append((s, lines)))
    opened = time.time()
    hub.add_session("win", LOCAL, opened_at=opened)
    _feed(hub, "app",
          "%s 1 INFO db x: before" % _stamp(-60, opened),
          "%s 1 INFO db x: after" % _stamp(+1, opened))
    hub._flush()
    assert [e.text.rsplit(": ", 1)[-1] for _, lines in batches for e in lines] == ["after"]


def test_two_windows_on_one_environment_share_one_reader(hub_factory):
    # Ten windows on a stand must not open ten ssh connections.
    _FakeReader.made = []
    source = _source(format="odoo")
    hub = hub_factory([source])
    hub.add_session("win-a", LOCAL, opened_at=time.time())
    hub.add_session("win-b", LOCAL, opened_at=time.time())
    hub.start()
    try:
        deadline = time.time() + 2
        while not _FakeReader.made and time.time() < deadline:
            time.sleep(0.01)
        assert len(_FakeReader.made) == 1
    finally:
        hub.close()


def test_both_windows_on_one_environment_receive_the_line(hub_factory):
    got = []
    source = _source(format="odoo")
    hub = hub_factory([source], on_lines=lambda s, l, lines: got.append(s))
    opened = time.time() - 10
    hub.add_session("win-a", LOCAL, opened_at=opened)
    hub.add_session("win-b", LOCAL, opened_at=opened)
    _feed(hub, "app", "%s 1 INFO db x: hello" % _stamp(0))
    hub._flush()
    assert sorted(got) == ["win-a", "win-b"]


def test_a_window_on_another_environment_is_not_offered_the_line(hub_factory):
    got = []
    hub = hub_factory([_source(format="odoo", envs=[LOCAL])],
                      on_lines=lambda s, l, lines: got.append(s))
    hub.add_session("local-win", LOCAL, opened_at=time.time() - 10)
    hub.add_session("dev-win", DEV, opened_at=time.time() - 10)
    _feed(hub, "app", "%s 1 INFO db x: hello" % _stamp(0))
    hub._flush()
    assert got == ["local-win"]


def test_the_log_name_travels_with_the_batch(hub_factory):
    seen = []
    sources = [_source(name="app", format="odoo"),
               _source(name="nginx", format="odoo")]
    hub = hub_factory(sources, on_lines=lambda s, l, lines: seen.append(l))
    hub.add_session("win", LOCAL, opened_at=time.time() - 10)
    _feed(hub, "nginx", "%s 1 INFO db x: hello" % _stamp(0))
    hub._flush()
    assert seen == ["nginx"]


def test_a_burst_is_trimmed_on_screen_and_says_how_much_was_dropped(hub_factory):
    batches = []
    hub = hub_factory([_source(format="odoo")], batch_max=3,
                      on_lines=lambda s, l, lines: batches.append(lines))
    hub.add_session("win", LOCAL, opened_at=time.time() - 10)
    _feed(hub, "app", *["%s 1 INFO db x: line %d" % (_stamp(0), i) for i in range(10)])
    hub._flush()
    texts = [entry.text for entry in batches[0]]
    assert len(texts) == 4                       # 3 lines + the notice
    assert "7 lines dropped" in texts[-1]


def test_the_report_keeps_every_line_the_screen_dropped(hub_factory):
    # The ceiling is a rate limit on the event stream, not on the evidence.
    hub = hub_factory([_source(format="odoo")], batch_max=3)
    hub.add_session("win", LOCAL, opened_at=time.time() - 10)
    started = time.time() - 5
    _feed(hub, "app", *["%s 1 INFO db x: line %d" % (_stamp(0), i) for i in range(10)])
    assert len(hub.slice(LOCAL, started)["app"]) == 10


def test_the_buffer_forgets_its_oldest_lines_not_its_newest(hub_factory):
    # A run that outlives the buffer must keep what is next to the failure.
    hub = hub_factory([_source(format="odoo")], buffer_lines=5)
    hub.add_session("win", LOCAL, opened_at=time.time() - 10)
    _feed(hub, "app", *["%s 1 INFO db x: line %d" % (_stamp(0), i) for i in range(9)])
    kept = hub.slice(LOCAL, time.time() - 60)["app"]
    assert [line.rsplit(" ", 1)[-1] for line in kept] == ["4", "5", "6", "7", "8"]


def test_the_report_slice_is_the_scenarios_own_window(hub_factory):
    hub = hub_factory([_source(format="odoo")])
    hub.add_session("win", LOCAL, opened_at=time.time() - 100)
    base = time.time()
    _feed(hub, "app",
          "%s 1 INFO db x: earlier" % _stamp(-50, base),
          "%s 1 INFO db x: during" % _stamp(-5, base),
          "%s 1 INFO db x: later" % _stamp(+50, base))
    lines = hub.slice(LOCAL, base - 10, base + 10)["app"]
    assert [line.rsplit(": ", 1)[-1] for line in lines] == ["during"]


def test_a_slice_can_be_asked_for_by_session_name(hub_factory):
    # What the runner has when it writes a report: a session name, not an env.
    hub = hub_factory([_source(format="odoo")])
    hub.add_session("win", LOCAL, opened_at=time.time() - 100)
    _feed(hub, "app", "%s 1 INFO db x: hello" % _stamp(0))
    assert hub.slice_for_session("win", time.time() - 60)["app"]
    assert hub.slice_for_session("never-opened", time.time() - 60) == {}


def test_an_empty_batch_announces_nothing(hub_factory):
    calls = []
    hub = hub_factory([_source(format="odoo")], on_lines=lambda *a: calls.append(a))
    hub.add_session("win", LOCAL, opened_at=time.time())
    hub._flush()
    assert calls == []


def test_a_consumer_that_raises_does_not_stop_the_tail(hub_factory):
    def explode(*_args):
        raise RuntimeError("the front-end went away")

    hub = hub_factory([_source(format="odoo")], on_lines=explode)
    hub.add_session("win", LOCAL, opened_at=time.time() - 10)
    _feed(hub, "app", "%s 1 INFO db x: hello" % _stamp(0))
    hub._flush()                                  # must not raise
    assert hub.slice(LOCAL, time.time() - 60)["app"]


# ------------------------------------------------- a log that cannot be read
class _BrokenReader(sl._Reader):
    def lines(self, stop):
        raise OSError("ssh: Could not resolve hostname")
        yield  # pragma: no cover - makes this a generator


def test_one_unreachable_log_does_not_take_the_others_down(caplog):
    good, bad = _source(name="app", format="odoo"), _source(name="nginx", format="odoo")

    def factory(source):
        return _BrokenReader(source) if source.name == "nginx" else _FakeReader(source)

    got = []
    hub = sl.ServerLogHub([good, bad], reader_factory=factory,
                          batch_seconds=0.02, on_lines=lambda s, l, lines: got.append(l))
    hub.add_session("win", LOCAL, opened_at=time.time() - 10)
    hub.start()
    try:
        for reader in _FakeReader.made:
            if reader.source is good:
                reader.feed.append("%s 1 INFO db x: still here" % _stamp(0))
        deadline = time.time() + 3
        while not got and time.time() < deadline:
            time.sleep(0.02)
    finally:
        hub.close()
    assert got and got[0] == "app"
    assert "nginx" in hub.unavailable()


def test_a_launch_is_never_stopped_by_a_log(hub_factory):
    # Constructing and starting a hub over an unreachable everything still returns.
    hub = sl.ServerLogHub([_source(format="odoo")], reader_factory=_BrokenReader,
                          batch_seconds=0.02)
    hub.start()
    hub.close()


# ---------------------------------------------------- the local file reader
def _drain(path, *, act, settle=0.45):
    """Follow ``path`` for real while ``act`` writes to it; return the lines seen."""
    seen = []
    source = _source(type="file", path=str(path), format="iso")
    hub = sl.ServerLogHub([source], batch_seconds=0.05,
                          on_lines=lambda s, l, lines: seen.extend(e.text for e in lines))
    hub.start()
    hub.add_session("win", LOCAL, opened_at=0)
    try:
        time.sleep(settle)
        act()
        time.sleep(settle * 2)
    finally:
        hub.close()
    return seen


def test_a_file_is_followed_from_its_end_not_its_start(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("2026-08-19T09:00:00Z ancient\n")
    # Only what happens from now on can belong to a window opening now; replaying
    # the file would date every session back to whenever the log was created.
    seen = _drain(path, act=lambda: path.open("a").write("2026-08-19T10:00:00Z fresh\n"))
    assert [line.split()[-1] for line in seen] == ["fresh"]


def test_a_rotated_file_is_picked_up_again(tmp_path):
    path = tmp_path / "app.log"
    path.write_text("")

    def rotate():
        path.open("a").write("2026-08-19T10:00:00Z before\n")
        time.sleep(0.4)
        os.rename(str(path), str(path) + ".1")
        path.write_text("2026-08-19T10:00:01Z after\n")

    seen = _drain(path, act=rotate)
    assert [line.split()[-1] for line in seen] == ["before", "after"]


def test_a_file_truncated_and_immediately_regrown_is_not_read_mid_line(tmp_path):
    # The case size alone cannot catch: between the truncate and the write there
    # is no moment to observe, so by the next poll the file is already longer than
    # the old read position and looks like ordinary growth. Reading from that
    # stale offset hands out the tail of a line as if it were a line.
    path = tmp_path / "app.log"
    path.write_text("")

    def truncate():
        path.open("a").write("2026-08-19T10:00:00Z short\n")
        time.sleep(0.4)
        path.write_text("2026-08-19T10:00:01Z a-much-longer-replacement-line\n")

    seen = _drain(path, act=truncate)
    assert [line.split()[-1] for line in seen] == \
        ["short", "a-much-longer-replacement-line"]


def test_a_file_that_is_not_there_yet_does_not_crash_the_hub(tmp_path):
    hub = sl.ServerLogHub([_source(type="file", path=str(tmp_path / "later.log"))],
                          batch_seconds=0.02)
    hub.start()
    time.sleep(0.2)
    hub.close()
    assert "app" in hub.unavailable()


# ------------------------------------- a follow command that dies is an error
def _process_source(command, tmp_path):
    """A log whose reader runs ``command`` locally, whatever its declared type."""
    source = _source(type="docker", container="x",
                     connection=sl.Connection("here", "local"))
    source.command = lambda: command
    return source


def test_a_reader_whose_command_dies_reports_why(tmp_path):
    # The false green this exists to prevent: an unknown ssh host, a container
    # that is not running and a missing path all end the command in
    # milliseconds. A reader that merely returns makes every one of them look
    # like a log that had nothing to say.
    source = _process_source(["sh", "-c", "echo boom >&2; exit 3"], tmp_path)
    reader = sl._ProcessReader(source)
    with pytest.raises(OSError) as exc:
        list(reader.lines(threading.Event()))
    assert "boom" in str(exc.value)


def test_the_exit_status_is_reported_when_it_says_nothing(tmp_path):
    source = _process_source(["sh", "-c", "exit 7"], tmp_path)
    reader = sl._ProcessReader(source)
    with pytest.raises(OSError) as exc:
        list(reader.lines(threading.Event()))
    assert "status 7" in str(exc.value)


def test_an_ssh_warning_does_not_hide_the_real_reason(tmp_path):
    # ssh leads with "Warning: Identity file ... not accessible" and only then
    # says it could not resolve the host. Reporting the warning sends someone
    # off fixing the wrong thing.
    script = ("echo 'Warning: Identity file /x not accessible: No such file' >&2;"
              "echo 'ssh: Could not resolve hostname nx.invalid' >&2; exit 255")
    reader = sl._ProcessReader(_process_source(["sh", "-c", script], tmp_path))
    with pytest.raises(OSError) as exc:
        list(reader.lines(threading.Event()))
    assert "Could not resolve hostname" in str(exc.value)
    assert "Warning:" not in str(exc.value)


def test_stopping_on_purpose_is_not_an_error(tmp_path):
    # Closing down a run terminates every reader; that must not be reported as a
    # log that broke.
    stop = threading.Event()
    reader = sl._ProcessReader(_process_source(["sh", "-c", "echo one; sleep 5"],
                                               tmp_path))
    seen = []
    for line in reader.lines(stop):
        seen.append(line)
        stop.set()                      # as ServerLogHub.close() does
    assert seen == ["one\n"]


def test_a_dead_command_leaves_the_log_marked_unavailable(tmp_path):
    source = _process_source(["sh", "-c", "echo nope >&2; exit 1"], tmp_path)
    hub = sl.ServerLogHub([source], batch_seconds=0.02)
    hub.start()
    deadline = time.time() + 8
    while not hub.unavailable() and time.time() < deadline:
        time.sleep(0.05)
    hub.close()
    assert "nope" in hub.unavailable().get("app", "")


# ----------------------------------------- whole-second logs vs a mid-second window
def _second_stamped(hub, offset_s, base):
    """Feed one odoo line whose timestamp has no sub-second part at all."""
    moment = datetime.datetime.fromtimestamp(base + offset_s)
    _feed(hub, "app", "%s,000 1 INFO db x: line" % moment.strftime("%Y-%m-%d %H:%M:%S"))


def test_a_whole_second_log_is_not_lost_to_a_mid_second_window(hub_factory):
    """The bug an end-to-end run found and every unit test had missed.

    Plenty of formats are whole seconds - nginx, syslog, journalctl - and a window
    that opens at 12:00:00.75 was discarding everything stamped "12:00:00",
    including the lines written after it opened. A scenario that happened to start
    mid-second got an empty report.
    """
    got = []
    hub = hub_factory([_source(format="odoo")],
                      on_lines=lambda s, l, lines: got.extend(lines))
    base = int(time.time())              # a whole second
    hub.add_session("win", LOCAL, opened_at=base + 0.75)
    _second_stamped(hub, 0, base)        # written in the very second it opened
    hub._flush()
    assert len(got) == 1


def test_the_report_slice_covers_the_second_it_starts_in(hub_factory):
    hub = hub_factory([_source(format="odoo")])
    hub.add_session("win", LOCAL, opened_at=0)
    base = int(time.time())
    _second_stamped(hub, 0, base)
    assert hub.slice(LOCAL, base + 0.75, base + 0.9)["app"]


def test_the_grace_is_one_second_and_no_more(hub_factory):
    # It has to stay a rounding allowance, not a window that quietly widens.
    hub = hub_factory([_source(format="odoo")])
    hub.add_session("win", LOCAL, opened_at=0)
    base = int(time.time())
    _second_stamped(hub, -2, base)       # two seconds before the slice starts
    assert "app" not in hub.slice(LOCAL, base + 0.75)


# --------------------------------- a log problem must never cost the windows
def test_a_misspelled_name_keeps_the_logs_that_did_resolve(caplog):
    # Asking for two logs and getting one name wrong must not cost the other.
    chosen = sl.resolve(_config(), DEV, ["nginx", "ngnix"], strict=False)
    assert [s.name for s in chosen] == ["nginx"]


def test_a_tolerant_resolve_still_says_what_was_wrong(caplog):
    with caplog.at_level("WARNING"):
        sl.resolve(_config(), DEV, ["ngnix"], strict=False)
    assert "ngnix" in caplog.text


def test_strict_is_the_default_so_a_listing_still_raises():
    with pytest.raises(sl.ServerLogError):
        sl.resolve(_config(), DEV, ["ngnix"])


def test_a_pattern_that_will_not_compile_costs_that_log_and_no_other(caplog):
    # Building a stream compiles the log's patterns. Raising out of the hub's
    # constructor would take the whole launch down over one bad regex.
    good = _source(name="app", format="odoo")
    bad = _source(name="broken", timestamp={"regex": "([unclosed", "format": "iso"})
    with caplog.at_level("WARNING"):
        hub = sl.ServerLogHub([good, bad], reader_factory=_FakeReader)
    assert [s.name for s in hub.sources] == ["app"]
    assert "broken" in hub.unavailable()
    assert "broken" in caplog.text
    hub.close()


# ------------------------- a log whose clock disagrees with this machine's
def test_a_utc_log_on_a_shifted_machine_still_lands_in_the_window(caplog):
    """The failure this catches was total and silent.

    Odoo (and plenty else) writes UTC. On a UTC+3 machine every line then parsed
    three hours into the past, fell outside every session's window, and the run
    streamed nothing at all - with an empty report that looks exactly like a
    server that had nothing to say.
    """
    parser = sl.LineParser(_source(name="app", format="odoo"))
    utc = datetime.datetime.utcfromtimestamp(time.time())
    with caplog.at_level("WARNING"):
        ts, _level, _text = parser.parse(
            "%s,000 42 INFO db x: hello" % utc.strftime("%Y-%m-%d %H:%M:%S"))
    if abs(time.timezone) < 60 and not time.daylight:
        pytest.skip("this machine runs on UTC, so there is no drift to correct")
    assert abs(ts - time.time()) < 5           # read as written now
    assert "timestamps are" in caplog.text     # and said so
    assert '"tz"' in caplog.text               # with the proper fix named


def test_a_correct_timestamp_is_left_exactly_as_it_is():
    parser = sl.LineParser(_source(format="odoo"))
    local = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S,123")
    ts, _level, _text = parser.parse("%s 42 INFO db x: hello" % local)
    # Sub-second precision survives: the correction is a safety net, not a rounding.
    assert ts != int(ts)
    assert abs(ts - time.time()) < 5


def test_the_drift_is_reported_once_not_per_line(caplog):
    parser = sl.LineParser(_source(name="app", format="odoo"))
    stamp = datetime.datetime.fromtimestamp(time.time() - 86400)
    with caplog.at_level("WARNING"):
        for _ in range(5):
            parser.parse("%s,000 42 INFO db x: hello"
                         % stamp.strftime("%Y-%m-%d %H:%M:%S"))
    assert caplog.text.count("timestamps are") == 1


def test_an_explicit_utc_setting_needs_no_correction(caplog):
    # The proper fix, and it must leave the warning silent.
    source = _source(name="app", timestamp={
        "regex": r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
        "format": "%Y-%m-%d %H:%M:%S", "tz": "utc"})
    parser = sl.LineParser(source)
    utc = datetime.datetime.utcfromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
    with caplog.at_level("WARNING"):
        ts, _level, _text = parser.parse("%s 42 INFO db x: hello" % utc)
    assert abs(ts - time.time()) < 5
    assert "timestamps are" not in caplog.text


def test_a_continuation_line_follows_the_corrected_time(caplog):
    # Otherwise a traceback's body would sit three hours from its own header.
    parser = sl.LineParser(_source(name="app", format="odoo"))
    utc = datetime.datetime.utcfromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
    with caplog.at_level("WARNING"):
        head, _, _ = parser.parse("%s,000 42 ERROR db x: Traceback:" % utc)
        body, _, _ = parser.parse("  File \"/x.py\", line 1")
    assert head == body


def test_a_logs_own_timezone_overrides_the_preset(caplog):
    """One word instead of a hand-copied timestamp block.

    The shape of a line and the clock it was written by are different questions,
    and only the second one changes per deployment - so it is a field of its own
    rather than something buried inside a custom pattern.
    """
    config = sl.parse_config({
        "connections": [{"name": "here", "type": "local"}],
        "logs": [{"name": "app", "connection": "here", "env": LOCAL, "path": "/x",
                  "format": "odoo", "tz": "utc"}]})
    parser = sl.LineParser(config.logs[0])
    utc = datetime.datetime.utcfromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
    with caplog.at_level("WARNING"):
        ts, _level, _text = parser.parse("%s,000 42 INFO db x: hello" % utc)
    assert abs(ts - time.time()) < 5
    assert "timestamps are" not in caplog.text     # nothing to correct any more


def test_an_unusable_timezone_names_the_log_it_is_on():
    with pytest.raises(sl.ServerLogError) as exc:
        sl.parse_config({"connections": [{"name": "here", "type": "local"}],
                         "logs": [{"name": "app", "connection": "here", "env": LOCAL,
                                   "path": "/x", "tz": "Europe/Kyiv"}]})
    assert "app" in str(exc.value) and "+HH:MM" in str(exc.value)


# ------------------------------------------- reading a log rather than following it
def _lines_file(tmp_path, count=2000):
    path = tmp_path / "app.log"
    path.write_text("".join("line %d\n" % i for i in range(1, count + 1)))
    return _source(type="file", path=str(path))


def test_reading_takes_the_end_by_default(tmp_path):
    lines, truncated = sl.read_lines(_lines_file(tmp_path))
    assert len(lines) == sl.READ_TAIL_LINES
    assert lines[-1] == "line 2000"
    assert truncated is True          # there is more above it, and it says so


def test_reading_all_of_a_small_log_is_not_truncated(tmp_path):
    lines, truncated = sl.read_lines(_lines_file(tmp_path, count=10), tail=None)
    assert lines == ["line %d" % i for i in range(1, 11)]
    assert truncated is False


def test_the_byte_budget_wins_over_asking_for_everything(tmp_path):
    # "Open the whole thing" has to mean "as much of the end as is sane to move".
    lines, truncated = sl.read_lines(_lines_file(tmp_path), tail=None, max_bytes=200)
    assert truncated is True
    assert lines[-1] == "line 2000"
    assert len(lines) < 2000


def test_a_budget_cut_drops_the_half_line_it_lands_on(tmp_path):
    # Showing half a line as though it were one is worse than showing one fewer.
    path = tmp_path / "app.log"
    path.write_text("aaaaaaaaaa\nbbbbbbbbbb\ncccccccccc\n")
    source = _source(type="file", path=str(path))
    lines, _truncated = sl.read_lines(source, tail=None, max_bytes=16)
    assert all(len(line) == 10 for line in lines)


def test_reading_a_file_that_is_not_there_says_why(tmp_path):
    with pytest.raises(OSError) as exc:
        sl.read_lines(_source(type="file", path=str(tmp_path / "nope.log")))
    assert "nope.log" in str(exc.value)


@pytest.mark.parametrize("kwargs, follow, expected", [
    (dict(type="file", path="/x.log"), False, ["tail", "-n", "20", "/x.log"]),
    (dict(type="docker", container="c"), False, ["docker", "logs", "-n", "20", "c"]),
    (dict(type="journal", unit="u"), False,
     ["journalctl", "--no-pager", "-n", "20", "-u", "u"]),
])
def test_a_one_shot_command_reads_and_stops(kwargs, follow, expected):
    assert _source(**kwargs).command(follow=follow, tail=20) == expected


def test_asking_for_everything_drops_the_line_count(tmp_path):
    assert _source(type="file", path="/x.log").command(follow=False) == \
        ["cat", "/x.log"]
    assert _source(type="docker", container="c").command(follow=False) == \
        ["docker", "logs", "c"]


def test_following_is_still_what_a_run_gets():
    # The run must start at the end: only what happens from now on can belong to
    # a window opening now.
    assert _source(type="file", path="/x.log").command() == \
        ["tail", "-n", "0", "-F", "/x.log"]


def test_a_command_that_fails_reports_its_complaint(tmp_path):
    source = _source(type="docker", container="c")
    source.command = lambda follow=True, tail=None: ["sh", "-c",
                                                     "echo 'no such container' >&2; exit 1"]
    with pytest.raises(OSError) as exc:
        sl.read_lines(source)
    assert "no such container" in str(exc.value)


# ------------------------------------------------------------------- levels
def test_the_five_levels_are_severity_ordered():
    assert sl.LEVELS == ("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")


@pytest.mark.parametrize("word, expected", [
    ("TRACE", "DEBUG"), ("debug", "DEBUG"), ("verbose", "DEBUG"),
    ("INFO", "INFO"), ("notice", "INFO"),
    ("warn", "WARN"), ("WARNING", "WARN"),
    ("err", "ERROR"), ("Error", "ERROR"), ("severe", "ERROR"),
    ("crit", "CRITICAL"), ("FATAL", "CRITICAL"), ("panic", "CRITICAL"),
    ("emerg", "CRITICAL"), ("alert", "CRITICAL"),
])
def test_whatever_a_backend_calls_it_lands_on_one_of_them(word, expected):
    assert sl.normalize_level(word) == expected


def test_an_unknown_word_is_information_not_an_alarm():
    # Guessing "severe" from a word nobody recognises would cry wolf in colour.
    assert sl.normalize_level("frobnicated") == "INFO"
    assert sl.normalize_level("") == "INFO"
    assert sl.normalize_level(None) == "INFO"


def test_a_critical_survives_all_the_way_to_the_panel():
    """It used to collapse into ERROR before anything could colour it.

    engine.overlay folds everything alarming into three buckets because the HUD
    is one small widget. A log viewer is the one place where "the process is
    going down" has to look different from "that request failed".
    """
    parser = sl.LineParser(_source(format="odoo"))
    line = "2026-08-19 10:53:09,123 42 CRITICAL db x: the database is gone"
    _ts, level, _text = parser.parse(line, seen_at=parser.timestamp_of(line))
    assert level == "CRITICAL"


def test_a_debug_line_stays_debug_rather_than_becoming_info():
    parser = sl.LineParser(_source(format="odoo"))
    line = "2026-08-19 10:53:09,123 42 DEBUG db x: chatter"
    _ts, level, _text = parser.parse(line, seen_at=parser.timestamp_of(line))
    assert level == "DEBUG"


def test_the_huds_own_three_buckets_are_left_alone():
    # engine.overlay's vocabulary is its own; widening it would change what the
    # in-page HUD paints, which is a different feature with different needs.
    from engine.overlay import normalize_level as hud_level
    assert hud_level("CRITICAL") == "ERROR"
    assert hud_level("DEBUG") == "INFO"
