import json
import os

import pytest

import session_launcher as sl

# The three environment shapes the real config uses: a bare host:port with no
# scheme, and full URLs with a trailing slash.
LOCAL = "localhost:8069"
DEV = "https://app-dev.example.com/"
STG = "https://app-stg.example.com/"


def _row(env, login, password="pw-test", cls=None, tests=()):
    return sl.User(env, cls or login.title(), login, password, tests)


def _envs(*values):
    return sl.build_environments(list(values))


def _config(tmp_path, entries, name="users.json"):
    path = tmp_path / name
    path.write_text(json.dumps(entries), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------- env values

def test_env_origin_bare_host_port_is_http():
    # urlsplit("localhost:8069") reads "localhost" as the scheme, which would make
    # the origin "localhost://" - the value has to be parsed protocol-relative.
    assert sl.env_origin(LOCAL) == "http://localhost:8069"


def test_env_origin_keeps_scheme_and_drops_trailing_slash():
    assert sl.env_origin(DEV) == "https://app-dev.example.com"


def test_env_origin_defaults_remote_hosts_to_https():
    assert sl.env_origin("app-dev.example.com") == "https://app-dev.example.com"


def test_env_origin_loopback_is_http():
    assert sl.env_origin("127.0.0.1:8069") == "http://127.0.0.1:8069"


def test_env_origin_plain_label_has_no_url():
    assert sl.env_origin("myprofile") == ""


def test_env_alias_uses_first_dns_label():
    assert sl.env_alias(DEV) == "app-dev"
    assert sl.env_alias(LOCAL) == "localhost"


def test_env_alias_keeps_ip_literals_whole():
    assert sl.env_alias("127.0.0.1:8069") == "127.0.0.1"
    assert sl.env_alias("[::1]:8069") == "::1"


def test_env_alias_is_case_insensitive():
    assert sl.env_alias("HTTPS://App-Dev.Example.Com/") == "app-dev"


# ------------------------------------------------------------- environments

def test_build_environments_dedupes_and_counts():
    envs = _envs(LOCAL, DEV, LOCAL, DEV, DEV)
    assert [e.alias for e in envs] == ["localhost", "app-dev"]  # first-seen order
    assert [e.count for e in envs] == [2, 3]


def test_build_environments_skips_empty_values():
    assert [e.value for e in _envs("", LOCAL, "")] == [LOCAL]


def test_build_environments_breaks_alias_collisions():
    envs = _envs("localhost:8069", "localhost:8070")
    assert sorted(e.alias for e in envs) == ["localhost:8069", "localhost:8070"]


def test_build_environments_aliases_are_unique():
    aliases = [e.alias for e in _envs(LOCAL, DEV, STG, "localhost:8070")]
    assert len(aliases) == len(set(aliases))


# --------------------------------------------------------------- resolution

def test_resolve_environment_exact_alias():
    assert sl.resolve_environment("app-dev", _envs(LOCAL, DEV, STG), "x").value == DEV


def test_resolve_environment_unambiguous_shortening():
    envs = _envs(LOCAL, DEV, STG)
    assert sl.resolve_environment("dev", envs, "x").value == DEV
    assert sl.resolve_environment("stg", envs, "x").value == STG
    assert sl.resolve_environment("local", envs, "x").value == LOCAL


def test_resolve_environment_accepts_the_full_config_value():
    # The migration path: an old --filter-prefix string pasted into --env.
    assert sl.resolve_environment(DEV, _envs(LOCAL, DEV, STG), "x").value == DEV


def test_resolve_environment_accepts_the_origin():
    envs = _envs(LOCAL, DEV, STG)
    assert sl.resolve_environment("https://app-dev.example.com", envs, "x").value == DEV


def test_resolve_environment_is_case_insensitive():
    assert sl.resolve_environment("APP-DEV", _envs(LOCAL, DEV, STG), "x").value == DEV


def test_resolve_environment_ambiguous_names_the_candidates():
    with pytest.raises(SystemExit) as exc:
        sl.resolve_environment("claim", _envs(LOCAL, DEV, STG), "x")
    assert "app-dev" in str(exc.value) and "app-stg" in str(exc.value)


def test_resolve_environment_unknown_lists_every_environment():
    with pytest.raises(SystemExit) as exc:
        sl.resolve_environment("prd", _envs(LOCAL, DEV, STG), "users.json")
    message = str(exc.value)
    assert "app-dev" in message and "app-stg" in message and "localhost" in message


def test_resolve_environment_all_is_rejected_with_a_hint():
    with pytest.raises(SystemExit) as exc:
        sl.resolve_environment("all", _envs(LOCAL, DEV), "x")
    assert "exactly one environment" in str(exc.value)


def test_resolve_environment_empty_is_rejected():
    with pytest.raises(SystemExit):
        sl.resolve_environment("", _envs(LOCAL, DEV), "x")


# ---------------------------------------------------------------- url + args

def test_normalize_url_points_bare_and_login_urls_at_web():
    for path in ("", "/", "/web/login", "/web/login/"):
        assert sl.normalize_url("http://localhost:8069" + path) == "http://localhost:8069/web"


def test_normalize_url_leaves_deep_links_alone():
    deep = "https://app.example.com/orders/12?x=1#tab"
    assert sl.normalize_url(deep) == deep


def test_parse_filter_list_all_means_no_filter():
    assert sl.parse_filter_list("--filter-users", "all") is None
    assert sl.parse_filter_list("--filter-users", " ALL ") is None


def test_parse_filter_list_splits_and_trims():
    assert sl.parse_filter_list("--filter-users", " a , b ") == ["a", "b"]


def test_parse_filter_list_rejects_empty():
    # The original bug: --filter-users="$VAR" with VAR unset used to mean "all".
    with pytest.raises(SystemExit):
        sl.parse_filter_list("--filter-users", "")


def test_parse_filter_list_rejects_separators_only():
    with pytest.raises(SystemExit):
        sl.parse_filter_list("--filter-users", ",,")


# ------------------------------------------------------------ profile folders

def test_adhoc_session_prefix_uses_the_selected_env():
    envs = _envs(LOCAL, DEV)
    env = sl.resolve_environment("dev", envs, "x")
    assert sl.adhoc_session_prefix(env, "http://localhost:8069", envs,
                                   "http://localhost:8069/web") == DEV


def test_adhoc_session_prefix_falls_back_to_the_matching_origin():
    envs = _envs(LOCAL, DEV)
    assert sl.adhoc_session_prefix(None, "https://app-dev.example.com", envs,
                                   "https://app-dev.example.com/web") == DEV


def test_adhoc_session_prefix_unknown_origin_uses_the_netloc():
    assert sl.adhoc_session_prefix(None, "http://other:1234", _envs(LOCAL, DEV),
                                   "http://other:1234/web") == "other:1234"


def test_session_dir_for_flattens_url_separators():
    assert sl.session_dir_for("", DEV, "role_agent") == "https:_app-dev.example.com_-role_agent"


def test_session_dir_for_user_session_overrides_the_env():
    assert sl.session_dir_for("scratch", DEV, "role_agent") == "scratch-role_agent"


def test_session_dir_for_sanitizes_the_login():
    assert sl.session_dir_for("", LOCAL, "Test.User@example.com") == (
        "localhost:8069-Test.User_example.com")


def test_session_dir_for_without_env_is_the_bare_login():
    assert sl.session_dir_for("", "", "admin") == "admin"


# ------------------------------------------------------------------ passwords

def test_env_shared_password_majority_wins_over_an_outlier():
    # The real dev shape: 10 users on one password, one admin on another.
    users = [_row(DEV, "u%d" % i) for i in range(10)] + [_row(DEV, "admin", "other")]
    assert sl.env_shared_password(users, DEV) == ("pw-test", 10, 11)


def test_env_shared_password_ignores_other_environments():
    users = [_row(DEV, "a"), _row(LOCAL, "b", "nope"), _row(LOCAL, "c", "nope")]
    assert sl.env_shared_password(users, DEV)[0] == "pw-test"


def test_env_shared_password_none_without_a_majority():
    users = [_row(DEV, "a", "one"), _row(DEV, "b", "two")]
    assert sl.env_shared_password(users, DEV) is None


def test_apply_password_override_replaces_only_the_password():
    users = [_row(DEV, "a"), _row(LOCAL, "b")]
    assert sl.apply_password_override(users, "new") == [
        _row(DEV, "a", "new"), _row(LOCAL, "b", "new")]


# -------------------------------------------------------------- --user rows

def _resolve(login, password, users, env=None, origin="https://app-dev.example.com"):
    envs = _envs(*[u.env for u in users]) if users else _envs(LOCAL, DEV, STG)
    selected = sl.resolve_environment(env, envs, "users.json") if env else None
    return sl.resolve_user_row(login, password, users, selected, origin, envs,
                               "users.json", origin + "/web")


def test_resolve_user_row_takes_the_matching_config_row():
    users = [_row(DEV, "role_division", "pw-test", cls="Division"), _row(LOCAL, "admin", "admin")]
    assert _resolve("role_division", None, users, env="dev") == sl.User(
        DEV, "Division", "role_division", "pw-test")


def test_resolve_user_row_explicit_password_wins_over_the_config():
    users = [_row(DEV, "role_division", "pw-test", cls="Division")]
    row = _resolve("role_division", "typed", users, env="dev")
    assert (row.env, row.login, row.password) == (DEV, "role_division", "typed")


def test_resolve_user_row_unknown_login_borrows_the_env_password():
    # Not an error: --env holds prepared credentials, --user just overrides which
    # account they are used for.
    users = [_row(DEV, "a"), _row(DEV, "b"), _row(DEV, "admin", "other")]
    assert _resolve("role_agent3", None, users, env="dev") == sl.User(
        DEV, "role_agent3", "role_agent3", "pw-test")


def test_resolve_user_row_scopes_by_url_origin_without_env():
    users = [_row(DEV, "dup", "devpass"), _row(LOCAL, "dup", "localpass")]
    row = _resolve("dup", None, users, origin="http://localhost:8069")
    assert row == sl.User(LOCAL, "Dup", "dup", "localpass")


def test_resolve_user_row_ambiguous_login_names_the_environments():
    users = [_row(DEV, "dup"), _row(STG, "dup")]
    with pytest.raises(SystemExit) as exc:
        _resolve("dup", None, users, origin="http://nowhere:1")
    assert "app-dev" in str(exc.value) and "app-stg" in str(exc.value)


def test_resolve_user_row_without_a_majority_password_asks_for_one():
    users = [_row(DEV, "a", "one"), _row(DEV, "b", "two")]
    with pytest.raises(SystemExit) as exc:
        _resolve("new", None, users, env="dev")
    assert "--password" in str(exc.value)


def test_resolve_user_row_unknown_login_with_password_needs_no_config():
    assert _resolve("brand_new", "pw", [], env=None) == sl.User(
        DEV, "brand_new", "brand_new", "pw")


# ------------------------------------------------------------------- config

def test_load_users_reads_the_env_field(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p"}])
    assert sl.load_users(path) == [sl.User(DEV, "A", "a", "p")]


def test_load_users_still_reads_the_legacy_prefix_field(tmp_path):
    path = _config(tmp_path, [{"prefix": DEV, "class": "A", "login": "a", "password": "p"}])
    assert sl.load_users(path) == [sl.User(DEV, "A", "a", "p")]


def test_load_users_rejects_conflicting_env_and_prefix(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "prefix": LOCAL, "class": "A",
                               "login": "a", "password": "p"}])
    with pytest.raises(SystemExit) as exc:
        sl.load_users(path)
    assert "'env'" in str(exc.value)


def test_load_users_defaults_env_to_empty(tmp_path):
    path = _config(tmp_path, [{"class": "A", "login": "a", "password": "p"}])
    assert sl.load_users(path) == [sl.User("", "A", "a", "p")]


def test_load_users_rejects_duplicate_env_and_login(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p"},
                              {"env": DEV, "class": "B", "login": "a", "password": "q"}])
    with pytest.raises(SystemExit) as exc:
        sl.load_users(path)
    assert "duplicate" in str(exc.value)


def test_load_users_allows_the_same_login_in_two_environments(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p"},
                              {"env": LOCAL, "class": "A", "login": "a", "password": "p"}])
    assert len(sl.load_users(path)) == 2


def test_load_users_rejects_a_missing_key(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "login": "a", "password": "p"}])
    with pytest.raises(SystemExit):
        sl.load_users(path)


def test_load_users_rejects_a_non_list(tmp_path):
    path = _config(tmp_path, {"env": DEV})
    with pytest.raises(SystemExit):
        sl.load_users(path)


def test_environments_from_config_is_soft_when_not_strict(tmp_path):
    assert sl.environments_from_config(str(tmp_path / "missing.json"), strict=False) == []


# --------------------------------------------------------------- main() paths

# What main() exits with when there is no browser. These cases are meant to exit
# during argument handling, so reaching this message means parsing got all the
# way through - it is the sentinel for "nothing rejected the arguments".
_NO_CHROME = "Google Chrome was not found"


def _main(monkeypatch, config_path, *args):
    """Run main() with a throwaway config and return the SystemExit message.

    find_chrome is stubbed out unconditionally: these cases are meant to exit
    during argument handling, and a parser change that lets one slip through must
    fail at the browser lookup rather than launch real windows against the real
    profile dirs in user_sessions/.
    """
    monkeypatch.setattr(sl, "find_chrome", lambda: None)
    monkeypatch.setattr("sys.argv", ["session_launcher.py", "--config=" + config_path] + list(args))
    with pytest.raises(SystemExit) as exc:
        sl.main()
    return str(exc.value)


@pytest.fixture
def config(tmp_path):
    return _config(tmp_path, [
        {"env": LOCAL, "class": "Admin", "login": "admin", "password": "admin"},
        {"env": DEV, "class": "Division", "login": "role_division", "password": "pw-test"},
        {"env": STG, "class": "Division", "login": "role_division", "password": "pw-test"},
    ])


def test_main_rejects_the_retired_filter_prefix(monkeypatch, config):
    message = _main(monkeypatch, config, "--filter-prefix=" + DEV, "--url=http://x")
    assert "replaced by --env" in message


def test_main_rejects_empty_filter_prefix(monkeypatch, config):
    # An unset $PREFIX used to silently launch every environment.
    message = _main(monkeypatch, config, "--filter-prefix=", "--url=http://x")
    assert "replaced by --env" in message


def test_main_rejects_unknown_env(monkeypatch, config):
    assert "no environment matches" in _main(monkeypatch, config, "--env=prd")


def test_main_rejects_empty_env(monkeypatch, config):
    assert "--env" in _main(monkeypatch, config, "--env=")


def test_main_rejects_empty_filter_users(monkeypatch, config):
    assert "is empty" in _main(monkeypatch, config, "--env=dev", "--filter-users=")


def test_main_rejects_user_with_filter_users(monkeypatch, config):
    message = _main(monkeypatch, config, "--env=dev", "--user=admin",
                    "--filter-users=role_division")
    assert "not compatible with --user" in message


def test_main_requires_a_url_without_env(monkeypatch, config):
    assert "Usage:" in _main(monkeypatch, config)


# ------------------------------------------------------- per-user "run-tests"

def test_load_users_reads_run_tests_as_a_list(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p",
                               "run-tests": ["access_agent", "tag:repro"]}])
    assert sl.load_users(path)[0].tests == ("access_agent", "tag:repro")


def test_load_users_accepts_tests_as_a_shorthand_field(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p",
                               "tests": ["access_agent"]}])
    assert sl.load_users(path)[0].tests == ("access_agent",)


def test_load_users_splits_commas_in_run_tests(tmp_path):
    # The three spellings must mean the same thing, like --run-tests does.
    for value in ("access_agent,tag:repro", ["access_agent,tag:repro"],
                  ["access_agent", "tag:repro"]):
        path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p",
                                   "run-tests": value}], name="u%d.json" % id(value))
        assert sl.load_users(path)[0].tests == ("access_agent", "tag:repro")


def test_load_users_defaults_run_tests_to_empty(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p"}])
    assert sl.load_users(path)[0].tests == ()


def test_load_users_rejects_plural_tags_selector(tmp_path):
    # "tags:dev" would otherwise become a scenario id that does not exist and only
    # fail once the run is already under way.
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p",
                               "run-tests": ["tags:dev"]}])
    with pytest.raises(SystemExit) as exc:
        sl.load_users(path)
    assert "tag:dev" in str(exc.value)


def test_load_users_rejects_empty_run_tests(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p",
                               "run-tests": []}])
    with pytest.raises(SystemExit):
        sl.load_users(path)


def test_load_users_rejects_non_string_run_tests(tmp_path):
    path = _config(tmp_path, [{"env": DEV, "class": "A", "login": "a", "password": "p",
                               "run-tests": [12]}])
    with pytest.raises(SystemExit):
        sl.load_users(path)


def test_main_rejects_run_tests_config_when_nobody_has_any(monkeypatch, config):
    message = _main(monkeypatch, config, "--env=dev", "--run-tests=config")
    assert "none of the" in message and "run-tests" in message


# --------------------------------------------- options must never become the URL

def test_main_rejects_bare_run_tests(monkeypatch, config):
    # Regression: a valueless option fell through to the positional URL slot, so
    # the launcher opened Chrome on the literal string "--run-tests".
    message = _main(monkeypatch, config, "--env=dev", "--run-tests")
    assert "--run-tests needs a value" in message
    assert "config" in message


def test_main_rejects_unknown_option(monkeypatch, config):
    assert "Unknown option" in _main(monkeypatch, config, "--env=dev", "--nonsense")


def test_main_rejects_value_flags_without_a_value(monkeypatch, config):
    for flag in ("--user", "--filter-users", "--url", "--user-session"):
        message = _main(monkeypatch, config, "--env=dev", flag)
        assert "needs a value" in message and flag in message


def test_main_still_accepts_a_positional_url(monkeypatch, config):
    # The guard keys on a leading "-", so a real URL positional is unaffected. Stub
    # find_chrome so this stops at the browser lookup: past parsing is exactly what
    # is being asserted, and anything further would launch real windows.
    message = _main(monkeypatch, config, "http://localhost:8069")
    assert _NO_CHROME in message


# ------------------------------------------------------- DevToolsActivePort reset

def test_clear_devtools_port_removes_a_stale_file(tmp_path):
    # A SIGKILLed window leaves the file behind with a dead port; wait_for_devtools
    # would return it on the first poll and attach to nothing (or to whatever now
    # owns that port).
    stale = tmp_path / "DevToolsActivePort"
    stale.write_text("45123\n/devtools/browser/old\n", encoding="utf-8")
    sl.clear_devtools_port(str(tmp_path))
    assert not stale.exists()


def test_clear_devtools_port_is_a_noop_on_a_fresh_profile(tmp_path):
    sl.clear_devtools_port(str(tmp_path))          # must not raise
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------------ --jobs

def test_main_rejects_bare_jobs(monkeypatch, config):
    message = _main(monkeypatch, config, "--env=dev", "--run-tests=config", "--jobs")
    assert "--jobs needs a value" in message


def test_main_rejects_invalid_jobs(monkeypatch, config):
    for value in ("0", "-1", "abc", "2.5", "", "+2"):
        message = _main(monkeypatch, config, "--env=dev", "--run-tests=config",
                        "--jobs=" + value)
        assert "expected a positive number or 'all'" in message, value


def test_main_rejects_jobs_without_run_tests(monkeypatch, config):
    # --jobs only means something for autotest runs; a plain launch has nothing
    # to spread across workers.
    assert "--jobs requires --run-tests" in _main(monkeypatch, config, "--env=dev", "--jobs=4")


def test_main_rejects_jobs_1_without_run_tests(monkeypatch, config):
    # Even the default value is a typo when it stands alone.
    assert "--jobs requires --run-tests" in _main(monkeypatch, config, "--env=dev", "--jobs=1")


def test_main_accepts_jobs_all(monkeypatch, config):
    # Parsing lets it through; the run stops at the browser lookup (find_chrome is
    # stubbed by _main), so nothing launches. An explicit scenario id is used rather
    # than =config, because this fixture's users have no 'run-tests' field and that
    # check would fire first.
    message = _main(monkeypatch, config, "--env=dev", "--run-tests=access_agent", "--jobs=all")
    assert _NO_CHROME in message


def test_main_accepts_a_numeric_jobs(monkeypatch, config):
    message = _main(monkeypatch, config, "--env=dev", "--run-tests=access_agent", "--jobs=4")
    assert _NO_CHROME in message


# ------------------------------------------------- the scaffolded config must work

def test_scaffolded_config_loads(tmp_path, monkeypatch):
    """--init-users-json is the FIRST command a new user runs; its output must load.

    Regression: the template once wrote "tests": [], which load_users rejects (an
    empty list means nothing, so the field should be absent) - and a later version
    hand-quoted the JSON and produced a file that would not even parse. Scaffold
    and then load, so neither can come back.
    """
    path = tmp_path / "users.json"
    monkeypatch.setattr("sys.argv", ["session_launcher.py", "--config=" + str(path),
                                     "--init-users-json"])
    with pytest.raises(SystemExit):          # init_users_json exits after writing
        sl.main()

    users = sl.load_users(str(path))         # must not exit
    assert len(users) == 1
    assert users[0].login == "login1"
    assert users[0].tests == ()              # no run-tests key -> empty, not an error


def test_scaffolded_config_is_valid_json(tmp_path):
    path = tmp_path / "users.json"
    path.write_text(sl.DEFAULT_USERS_TEMPLATE, encoding="utf-8")
    entry = json.loads(path.read_text(encoding="utf-8"))[0]
    assert "run-tests" not in entry and "tests" not in entry


def test_users_example_json_is_shipped_and_loads():
    """The committed example is what the README points newcomers at."""
    example = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "users.example.json")
    assert os.path.exists(example), "users.example.json is missing"
    users = sl.load_users(example)
    assert users, "the example config must contain at least one user"
    assert any(u.tests for u in users), "the example should demonstrate run-tests"


def test_version_reports_something(monkeypatch, config):
    # Works from a source checkout (reports "dev") and when pip-installed.
    assert isinstance(sl.version(), str) and sl.version()
    monkeypatch.setattr("sys.argv", ["session_launcher.py", "--version"])
    with pytest.raises(SystemExit) as exc:
        sl.main()
    assert exc.value.code == 0


def test_version_is_not_duplicated_in_source():
    """pyproject.toml is the single source of truth for the version."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as fh:
        assert 'version = "' in fh.read()
    with open(os.path.join(root, "session_launcher.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "__version__" not in source, "second copy of the version - read it from metadata"


# -------------------------------------------------------------------- --extensions

def test_resolve_extension_by_friendly_name(tmp_path):
    # An EMPTY extensions dir, deliberately: with the real one, this depends on
    # whether somebody has vendored odoo_debug/ - which is exactly the precedence
    # rule under test elsewhere, not the store lookup under test here.
    ext = sl.resolve_extension("odoo_debug", extensions_dir=str(tmp_path))
    assert ext == ("odoo_debug", "store", sl.KNOWN_EXTENSIONS["odoo_debug"])


def test_resolve_extension_accepts_a_raw_store_id(tmp_path):
    # So a fork can install any extension without editing KNOWN_EXTENSIONS.
    raw = "abcdefghijklmnopabcdefghijklmnop"      # 32 chars from a-p
    assert sl.resolve_extension(raw, extensions_dir=str(tmp_path)) == (raw, "store", raw)


def test_resolve_extension_rejects_an_unknown_name():
    with pytest.raises(SystemExit) as exc:
        sl.resolve_extension("not_a_real_extension")
    message = str(exc.value)
    assert "odoo_debug" in message            # lists what IS known
    assert "32-character" in message          # and mentions the raw-id escape hatch


def test_resolve_extension_rejects_a_malformed_id():
    for bad in ("abc", "ABCDEFGHIJKLMNOPABCDEFGHIJKLMNOP", "z" * 32):
        with pytest.raises(SystemExit):
            sl.resolve_extension(bad)


def test_main_rejects_unknown_extension(monkeypatch, config):
    assert "unknown extension" in _main(monkeypatch, config, "--env=dev",
                                        "--extensions=nope")


def test_main_rejects_empty_extensions(monkeypatch, config):
    message = _main(monkeypatch, config, "--env=dev", "--extensions=")
    assert "Omit the flag to install nothing" in message
    # Must not advertise a nonexistent --extensions=all (parse_filter_list's wording).
    assert "--extensions=all" not in message


def test_main_rejects_bare_extensions(monkeypatch, config):
    assert "--extensions needs a value" in _main(monkeypatch, config, "--env=dev",
                                                 "--extensions")


def test_no_extensions_are_installed_by_default(monkeypatch, config):
    """The default is nothing: an automated profile carries only what the run needs.

    Proven by reaching the browser lookup with no CRX fetch attempted - download_crx
    would raise if it were called, since it is stubbed to fail here.
    """
    called = []
    monkeypatch.setattr(sl, "download_crx",
                        lambda *a, **k: called.append(a) or (_ for _ in ()).throw(
                            AssertionError("download_crx must not run by default")))
    message = _main(monkeypatch, config, "--env=dev", "--filter-users=role_division")
    assert _NO_CHROME in message
    assert called == []


def test_resolve_extension_accepts_name_equals_id():
    # Lets a script name an arbitrary extension without touching KNOWN_EXTENSIONS.
    raw = "fmkadmapgofadopljbjfkapdkoienihi"
    assert sl.resolve_extension("react_devtools=" + raw) == ("react_devtools", "store", raw)


def test_resolve_extension_rejects_a_malformed_name_equals_id():
    for bad in ("react_devtools=nope", "=fmkadmapgofadopljbjfkapdkoienihi", "x="):
        with pytest.raises(SystemExit) as exc:
            sl.resolve_extension(bad)
        assert "name=<32-char store id>" in str(exc.value)


def test_format_extensions_documents_discovery_and_the_escape_hatch():
    text = sl.format_extensions()
    assert "odoo_debug" in text                      # what is known
    assert "chromewebstore.google.com" in text       # where to find an id
    assert "KNOWN_EXTENSIONS" in text                # how to make a name permanent


def test_main_extensions_list_prints_and_exits(monkeypatch, config, capsys):
    monkeypatch.setattr("sys.argv", ["session_launcher.py", "--config=" + config,
                                     "--extensions=list"])
    with pytest.raises(SystemExit) as exc:
        sl.main()
    assert exc.value.code == 0
    assert "odoo_debug" in capsys.readouterr().out


def test_unknown_extension_error_shows_the_list(monkeypatch, config):
    message = _main(monkeypatch, config, "--env=dev", "--extensions=nope")
    assert "unknown extension" in message
    assert "chromewebstore.google.com" in message    # tells them how to proceed


# ------------------------------------------------- unpacked extensions in-tree

def _local_ext(tmp_path, name, version="1.0", **manifest):
    d = tmp_path / name
    d.mkdir(parents=True)
    base = {"manifest_version": 3, "name": name, "version": version}
    base.update(manifest)
    (d / "manifest.json").write_text(json.dumps(base), encoding="utf-8")
    return str(d)


def test_local_extensions_finds_only_dirs_with_a_manifest(tmp_path):
    _local_ext(tmp_path, "my_helper")
    (tmp_path / "notes").mkdir()                       # no manifest -> ignored
    (tmp_path / "README.md").write_text("hi", encoding="utf-8")
    assert list(sl.local_extensions(str(tmp_path))) == ["my_helper"]


def test_local_extensions_is_empty_when_the_dir_is_absent(tmp_path):
    assert sl.local_extensions(str(tmp_path / "nope")) == {}


def test_resolve_extension_prefers_a_local_copy(tmp_path):
    # A vendored copy pins/patches a store extension without renaming commands.
    _local_ext(tmp_path, "odoo_debug")
    ext = sl.resolve_extension("odoo_debug", extensions_dir=str(tmp_path))
    assert ext.kind == "local" and ext.value.endswith("odoo_debug")


def test_resolve_extension_falls_back_to_the_store(tmp_path):
    ext = sl.resolve_extension("odoo_debug", extensions_dir=str(tmp_path))
    assert ext.kind == "store" and ext.value == sl.KNOWN_EXTENSIONS["odoo_debug"]


def test_format_extensions_lists_local_ones_and_marks_the_override(tmp_path):
    _local_ext(tmp_path, "odoo_debug")
    _local_ext(tmp_path, "my_helper")
    text = sl.format_extensions(str(tmp_path))
    assert "my_helper" in text
    assert "overridden by the local copy" in text     # the odoo_debug store row
    assert "extensions/<name>/" in text               # how to add your own


def test_install_local_extension_copies_and_rewrites(tmp_path):
    """The source is copied into the profile and re-keyed there, not modified."""
    src = _local_ext(tmp_path, "my_helper", version="2.1",
                     update_url="https://clients2.google.com/service/update2/crx",
                     content_scripts=[{"matches": ["<all_urls>"], "js": ["c.js"]}])
    with open(os.path.join(src, "c.js"), "w", encoding="utf-8") as fh:
        fh.write("// noop")
    profile = tmp_path / "profile"
    profile.mkdir()
    ext_id = sl.install_local_extension(str(profile), src, "http://localhost:8069",
                                        ".my_helper_key")
    planted = profile / "Default" / "Extensions" / ext_id / "2.1_0"
    assert (planted / "c.js").exists(), "extension files were not copied"

    written = json.loads((planted / "manifest.json").read_text(encoding="utf-8"))
    assert "key" in written                                   # re-keyed
    assert "update_url" not in written                        # store update dropped
    assert written["content_scripts"][0]["matches"] == ["http://localhost:8069/*"]

    # The source on disk is untouched - only the copy inside the profile is rewritten.
    original = json.loads(open(os.path.join(src, "manifest.json"), encoding="utf-8").read())
    assert "key" not in original and "update_url" in original


def test_install_local_extension_refreshes_an_edited_source(tmp_path):
    # Same version, changed file: relaunching must pick the edit up.
    src = _local_ext(tmp_path, "my_helper")
    marker = os.path.join(src, "c.js")
    open(marker, "w", encoding="utf-8").write("v1")
    profile = tmp_path / "profile"
    profile.mkdir()
    ext_id = sl.install_local_extension(str(profile), src, "http://x", ".k")
    open(marker, "w", encoding="utf-8").write("v2")
    sl.install_local_extension(str(profile), src, "http://x", ".k")
    planted = profile / "Default" / "Extensions" / ext_id / "1.0_0" / "c.js"
    assert planted.read_text(encoding="utf-8") == "v2"


# ------------------------------------------------------- auto-login extension

def test_autologin_source_is_shipped_and_carries_no_secrets():
    """The behaviour is checked-in, editable JS; credentials are generated per profile."""
    src = sl.AUTOLOGIN_SRC
    assert os.path.isfile(os.path.join(src, "manifest.json"))
    content = open(os.path.join(src, "content.js"), encoding="utf-8").read()
    assert "AUTOLOGIN.login" in content and "AUTOLOGIN.password" in content
    # No literal credential and no hardcoded selector left in the source.
    assert 'input[name="login"]' not in content
    assert not os.path.exists(os.path.join(src, "config.js")), "config.js is generated"


def test_autologin_is_not_offered_as_an_installable_extension(tmp_path):
    # Underscore-prefixed: it is machinery, useless without its generated config.
    (tmp_path / "_autologin").mkdir()
    (tmp_path / "_autologin" / "manifest.json").write_text("{}", encoding="utf-8")
    assert sl.local_extensions(str(tmp_path)) == {}


def test_write_autologin_extension_generates_config_and_patches_the_manifest(tmp_path):
    out = tmp_path / "ext"
    sl.write_autologin_extension(str(out), "http://localhost:8069", "admin", "s3cr3t",
                                 key_b64="KEY")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "Auto-login (admin)"
    assert manifest["content_scripts"][0]["matches"] == ["http://localhost:8069/*"]
    assert manifest["content_scripts"][0]["js"] == ["config.js", "content.js"]
    assert manifest["key"] == "KEY"

    config = (out / "config.js").read_text(encoding="utf-8")
    assert '"password": "s3cr3t"' in config
    assert config.startswith("var AUTOLOGIN = ")
    assert (out / "content.js").exists()      # behaviour copied from the source


def test_write_autologin_extension_escapes_an_awkward_password(tmp_path):
    # json.dumps handles quotes, backslashes and newlines; a naive template would not.
    out = tmp_path / "ext"
    nasty = 'a"b\\c\nd</script>'
    sl.write_autologin_extension(str(out), "http://x", "u", nasty)
    config = (out / "config.js").read_text(encoding="utf-8")
    parsed = json.loads(config[len("var AUTOLOGIN = "):].rstrip().rstrip(";"))
    assert parsed["password"] == nasty


def test_write_autologin_extension_selectors_are_overridable(tmp_path):
    out = tmp_path / "ext"
    sl.write_autologin_extension(str(out), "http://x", "u", "p",
                                 selectors={"login": "#username"})
    config = json.loads((out / "config.js").read_text(encoding="utf-8")
                        [len("var AUTOLOGIN = "):].rstrip().rstrip(";"))
    assert config["selectors"]["login"] == "#username"
    # unspecified ones keep their defaults
    assert config["selectors"]["password"] == sl.DEFAULT_LOGIN_SELECTORS["password"]


def test_write_autologin_extension_refreshes_a_stale_install(tmp_path):
    out = tmp_path / "ext"
    sl.write_autologin_extension(str(out), "http://x", "u", "p")
    (out / "leftover.js").write_text("stale", encoding="utf-8")
    sl.write_autologin_extension(str(out), "http://x", "u", "p")
    assert not (out / "leftover.js").exists(), "the install dir should be refreshed"


# ------------------------------- default = every local extension, broken ones skipped

def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_validate_local_extension_accepts_a_good_one(tmp_path):
    src = _local_ext(tmp_path, "good")
    assert sl.validate_local_extension(src) == (True, "")


def test_validate_local_extension_rejects_broken_manifests(tmp_path):
    (tmp_path / "bad_json").mkdir()
    _write(str(tmp_path / "bad_json" / "manifest.json"), "{ not json")
    ok, why = sl.validate_local_extension(str(tmp_path / "bad_json"))
    assert not ok and "not valid JSON" in why

    (tmp_path / "no_keys").mkdir()
    _write(str(tmp_path / "no_keys" / "manifest.json"), '{"name": "x"}')
    ok, why = sl.validate_local_extension(str(tmp_path / "no_keys"))
    assert not ok and "missing" in why

    src = _local_ext(tmp_path, "old_mv", manifest_version=1)
    ok, why = sl.validate_local_extension(src)
    assert not ok and "not supported by Chrome" in why


def test_validate_local_extension_rejects_a_missing_content_script(tmp_path):
    src = _local_ext(tmp_path, "ghost",
                     content_scripts=[{"matches": ["<all_urls>"], "js": ["nope.js"]}])
    ok, why = sl.validate_local_extension(src)
    assert not ok and "nope.js" in why


def test_default_extensions_is_every_usable_local_one(tmp_path):
    _local_ext(tmp_path, "alpha")
    _local_ext(tmp_path, "beta")
    names = [e.name for e in sl.default_extensions(str(tmp_path))]
    assert names == ["alpha", "beta"]
    assert all(e.kind == "local" for e in sl.default_extensions(str(tmp_path)))


def test_default_extensions_skips_broken_ones_and_keeps_the_rest(caplog, tmp_path):
    # A bad extension should cost you that extension, not the launch.
    _local_ext(tmp_path, "good")
    (tmp_path / "bad").mkdir()
    _write(str(tmp_path / "bad" / "manifest.json"), "{ not json")
    with caplog.at_level("WARNING"):
        names = [e.name for e in sl.default_extensions(str(tmp_path))]
    assert names == ["good"]
    assert any("Skipping extension bad" in r.getMessage() for r in caplog.records)


def test_default_extensions_is_empty_without_an_extensions_dir(tmp_path):
    assert sl.default_extensions(str(tmp_path / "nope")) == []


def test_main_extensions_none_installs_nothing(monkeypatch, config):
    # Reaching the browser lookup proves parsing accepted it; the behaviour itself is
    # covered end to end by the launch checks.
    assert _NO_CHROME in _main(monkeypatch, config, "--env=dev",
                                                "--filter-users=role_division",
                                                "--extensions=none")


def test_main_extensions_all_is_accepted(monkeypatch, config):
    assert _NO_CHROME in _main(monkeypatch, config, "--env=dev",
                                                "--filter-users=role_division",
                                                "--extensions=all")


# ------------------------------------------------------------------ --describe

def _describe(monkeypatch, config_path, *args, capsys=None):
    """Run main() with --describe and return the parsed JSON it printed."""
    monkeypatch.setattr(sl, "find_chrome", lambda: None)
    monkeypatch.setattr("sys.argv", ["session_launcher.py", "--config=" + config_path,
                                     "--describe"] + list(args))
    with pytest.raises(SystemExit) as exc:
        sl.main()
    out = capsys.readouterr().out
    return json.loads(out), exc.value.code


def test_describe_lists_environments_users_and_paths(monkeypatch, config, capsys):
    payload, code = _describe(monkeypatch, config, capsys=capsys)
    assert code == 0
    assert [e["alias"] for e in payload["envs"]] == ["localhost", "app-dev", "app-stg"]
    assert [u["login"] for u in payload["users"]] == ["admin", "role_division",
                                                      "role_division"]
    assert payload["config_path"] == config
    # The choices a front-end renders as pickers come from here, not from a
    # second copy of the flag definitions.
    assert payload["log_levels"] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert "tree" in payload["overlay_components"]
    assert "screen" in payload["report_artifacts"]


def test_describe_never_emits_a_password(monkeypatch, tmp_path, capsys):
    path = _config(tmp_path, [{"env": LOCAL, "class": "Admin", "login": "admin",
                               "password": "s3cr3t-not-in-json"}])
    payload, _code = _describe(monkeypatch, path, capsys=capsys)
    assert "s3cr3t-not-in-json" not in json.dumps(payload)
    assert payload["users"][0]["has_password"] is True
    assert "password" not in payload["users"][0]


def test_describe_reports_the_profile_folder_each_user_will_use(monkeypatch, config,
                                                                capsys):
    payload, _code = _describe(monkeypatch, config, capsys=capsys)
    assert payload["users"][0]["profile"] == sl.session_dir_for("", LOCAL, "admin")


def test_describe_survives_an_unreadable_config(monkeypatch, tmp_path, capsys):
    # A front-end asking "what is there?" must get an answer it can render, not a
    # plain-text death - the message belongs in the payload.
    path = str(tmp_path / "broken.json")
    _write(path, "{ not json")
    payload, code = _describe(monkeypatch, path, capsys=capsys)
    assert code == 0
    assert payload["users"] == [] and payload["envs"] == []
    assert payload["warnings"] and "broken.json" in payload["warnings"][0]


def test_describe_reads_scenarios_from_flows_dir(monkeypatch, tmp_path, config, capsys):
    flows = tmp_path / "flows" / "scenarios"
    flows.mkdir(parents=True)
    _write(str(flows / "demo.yaml"),
           "id: demo\nname: Demo run\ntags: [smoke]\nsteps: []\n")
    _write(str(flows / "wip.yaml"),
           "id: wip\nname: Not in all\ntags: [manual]\nsteps: []\n")
    payload, _code = _describe(monkeypatch, config,
                               "--flows-dir=" + str(tmp_path / "flows"), capsys=capsys)
    by_id = {s["id"]: s for s in payload["scenarios"]}
    assert by_id["demo"]["name"] == "Demo run" and by_id["demo"]["in_all"] is True
    # tagged manual: runnable by id, but not by --run-tests=all
    assert by_id["wip"]["in_all"] is False
    assert payload["tags"] == ["manual", "smoke"]


# -------------------------------------------------------------------- --events

def test_main_rejects_an_empty_events_target(monkeypatch, config):
    assert "--events= is empty" in _main(monkeypatch, config, "--env=dev",
                                         "--events=", "--url=http://x")


def test_main_accepts_events_to_a_file(monkeypatch, config, tmp_path):
    # Reaching the browser lookup proves parsing accepted it.
    target = str(tmp_path / "events.jsonl")
    assert _NO_CHROME in _main(monkeypatch, config, "--env=dev",
                                                "--filter-users=role_division",
                                                "--events=" + target)


def test_events_needs_no_run_tests(monkeypatch, config):
    # Window lifecycle is worth watching on a plain launch too, so unlike the
    # flow-execution flags this one does not require --run-tests.
    assert _NO_CHROME in _main(monkeypatch, config, "--env=dev",
                                                "--filter-users=role_division",
                                                "--events=-")


# -------------------------------------------------------------- finding Chrome

def test_find_chrome_prefers_a_binary_that_answers_version(monkeypatch):
    # Ubuntu 22.04 ships `chromium-browser` as a 2 KB shim that only redirects to
    # a snap. It is on PATH and executable, so being findable proves nothing -
    # only answering --version does.
    monkeypatch.setattr(sl, "_chrome_candidates",
                        lambda: iter(["/usr/bin/chromium-browser",
                                      "/usr/bin/google-chrome"]))
    monkeypatch.setattr(sl, "_chrome_version_string",
                        lambda path: "Google Chrome 151" if "google" in path else "")
    assert sl.find_chrome() == "/usr/bin/google-chrome"


def test_find_chrome_falls_back_to_the_first_candidate(monkeypatch):
    # Nothing answered, but something is there. Keep returning it - an environment
    # that merely blocks --version must behave as it always has; describe_chrome
    # is what turns "present but silent" into a message.
    monkeypatch.setattr(sl, "_chrome_candidates", lambda: iter(["/usr/bin/chromium"]))
    monkeypatch.setattr(sl, "_chrome_version_string", lambda path: "")
    assert sl.find_chrome() == "/usr/bin/chromium"


def test_find_chrome_returns_none_when_there_is_nothing(monkeypatch):
    monkeypatch.setattr(sl, "_chrome_candidates", lambda: iter([]))
    assert sl.find_chrome() is None


def test_describe_chrome_flags_a_browser_that_will_not_run(monkeypatch):
    monkeypatch.setattr(sl, "find_chrome", lambda: "/usr/bin/chromium-browser")
    monkeypatch.setattr(sl, "_chrome_version_string", lambda path: "")
    chrome = sl.describe_chrome()
    assert chrome["path"] == "/usr/bin/chromium-browser"
    assert "does not run" in chrome["message"]


def test_describe_chrome_is_quiet_about_a_working_browser(monkeypatch):
    monkeypatch.setattr(sl, "find_chrome", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(sl, "_chrome_version_string", lambda path: "Google Chrome 151")
    assert sl.describe_chrome() == {"path": "/usr/bin/google-chrome",
                                    "version": "Google Chrome 151", "message": ""}


# ------------------------------------------------------- editing scenarios
# The four commands the Scenarios page is built on. The file format belongs to
# the engine (see tests/test_flowfile.py); what is checked here is the wiring -
# that each flag reaches it, that the answer is always JSON, and that the exit
# code says whether it worked, because the caller is a program.

FIXTURE_FLOWS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "fixtures", "flows")


@pytest.fixture
def flow_tree(tmp_path, monkeypatch):
    """A writable tree in front of the fixture tree, as an installed build has."""
    from engine import loader

    user = tmp_path / "user-flows"
    (user / "scenarios").mkdir(parents=True)
    monkeypatch.setattr(loader.runtime_paths, "flows_search_path",
                        lambda: [str(user), FIXTURE_FLOWS])
    return user


def _flow_cmd(monkeypatch, capsys, *args):
    """Run main() with the given flags; returns (exit_code, parsed JSON)."""
    monkeypatch.setattr("sys.argv", ["session_launcher.py"] + list(args))
    with pytest.raises(SystemExit) as exc:
        sl.main()
    out = capsys.readouterr().out
    return exc.value.code, json.loads(out)


def test_flow_show_returns_the_text_and_the_steps(monkeypatch, capsys, flow_tree):
    code, payload = _flow_cmd(monkeypatch, capsys, "--flow-show=demo_smoke")
    assert code == 0
    assert payload["source"] == "bundled" and not payload["writable"]
    assert [s["action"] for s in payload["steps"]]
    # The file as written, comments and all - the YAML view shows what is on
    # disk, not a re-rendering of it, so opening a scenario cannot lose anything.
    assert payload["yaml"].startswith("# The fixture equivalent")
    assert "id: demo_smoke" in payload["yaml"]


def test_flow_show_reports_a_missing_scenario_as_json(monkeypatch, capsys, flow_tree):
    # Never a bare traceback: the caller parses this.
    code, payload = _flow_cmd(monkeypatch, capsys, "--flow-show=no_such")
    assert code == 2 and payload["ok"] is False
    assert "no scenario" in payload["problems"][0]


def test_flow_save_writes_from_a_step_document(monkeypatch, capsys, flow_tree, tmp_path):
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps({"meta": {"name": "Written", "tags": ["smoke"]},
                               "steps": [{"action": "assert_visible",
                                          "target": "dashboard"}]}),
                   encoding="utf-8")
    code, payload = _flow_cmd(monkeypatch, capsys,
                              "--flow-save=written", "--from=%s" % doc)
    assert code == 0 and payload["ok"]
    assert (flow_tree / "scenarios" / "written.yaml").exists()


def test_flow_save_exits_non_zero_when_it_does_not_compile(monkeypatch, capsys,
                                                           flow_tree, tmp_path):
    doc = tmp_path / "doc.json"
    doc.write_text(json.dumps({"steps": [{"action": "nonsense", "target": "x"}]}),
                   encoding="utf-8")
    code, payload = _flow_cmd(monkeypatch, capsys,
                              "--flow-save=broken", "--from=%s" % doc)
    assert code == 1 and not payload["ok"]
    assert not (flow_tree / "scenarios" / "broken.yaml").exists()


def test_flow_save_without_from_says_so(monkeypatch, capsys, flow_tree):
    code, payload = _flow_cmd(monkeypatch, capsys, "--flow-save=x")
    assert code == 1 and "--from=FILE" in payload["problems"][0]


def test_flow_delete_refuses_a_bundled_scenario(monkeypatch, capsys, flow_tree):
    code, payload = _flow_cmd(monkeypatch, capsys, "--flow-delete=demo_smoke")
    assert code == 1 and not payload["ok"]
    assert "duplicate it instead" in payload["problems"][0]


def test_flow_import_copies_a_file_in(monkeypatch, capsys, flow_tree, tmp_path):
    source = tmp_path / "shared.yaml"
    source.write_text("id: shared\nname: Shared\ntags: []\nsteps:\n"
                      "  - assert_visible: dashboard\n", encoding="utf-8")
    code, payload = _flow_cmd(monkeypatch, capsys, "--flow-import=%s" % source)
    assert code == 0 and payload["ok"] and payload["id"] == "shared"
    assert (flow_tree / "scenarios" / "shared.yaml").exists()


def test_describe_says_which_scenarios_can_be_edited(monkeypatch, capsys, flow_tree,
                                                     config):
    # The page needs this to know whether to offer Save or only Duplicate.
    from engine import flowfile
    flowfile.save("mine", steps=[{"action": "assert_visible", "target": "dashboard"}])
    code, payload = _flow_cmd(monkeypatch, capsys, "--config=" + config, "--describe")
    assert code == 0
    by_id = {s["id"]: s for s in payload["scenarios"]}
    assert by_id["mine"]["source"] == "user" and by_id["mine"]["writable"]
    assert by_id["demo_smoke"]["source"] == "bundled"
    assert not by_id["demo_smoke"]["writable"]


def test_describe_advertises_the_step_grammar(monkeypatch, capsys, flow_tree, config):
    # So an editor's action menu cannot drift from what the compiler accepts.
    from engine import compiler

    _code, payload = _flow_cmd(monkeypatch, capsys, "--config=" + config, "--describe")
    actions = payload["flow_actions"]
    assert set(actions["selector_and_value"]) == compiler.SELECTOR_AND_VALUE
    assert set(actions["selector_only"]) == compiler.SELECTOR_ONLY
    assert "goto" in actions["url_target"] and "use" in actions["use"]
