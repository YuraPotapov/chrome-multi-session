"""Where the app reads and writes, in a checkout and in an installed build.

The whole point of runtime_paths is that these two answers differ, so the tests
are written the same way: each case pins one of the two shapes.
"""

import os

import pytest

import runtime_paths as rp


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    """Pretend to be an installed build with $HOME under tmp_path."""
    bundle = tmp_path / "opt" / "_internal"
    bundle.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(rp, "FROZEN", True)
    monkeypatch.setattr("sys._MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(rp.HOME_ENV, raising=False)
    return bundle, home


# ------------------------------------------------------------------ checkout

def test_a_checkout_keeps_everything_in_one_place():
    # The property the whole design rests on: nothing about a source checkout
    # changes, so no existing profile, report or users.json moves.
    root = os.path.dirname(os.path.abspath(rp.__file__))
    assert rp.app_root() == root
    assert rp.user_data_root() == root
    assert rp.sessions_dir() == os.path.join(root, "user_sessions")
    assert rp.config_path() == os.path.join(root, "users.json")


def test_a_checkout_is_not_given_a_users_json(tmp_path, monkeypatch):
    # Its absence is meaningful there - it is what sends you to --init-users-json.
    monkeypatch.setenv(rp.HOME_ENV, str(tmp_path))
    monkeypatch.setattr(rp, "app_root", lambda: str(tmp_path))
    rp.ensure_user_data_root()
    assert not (tmp_path / "users.json").exists()


# -------------------------------------------------------------------- frozen

def test_frozen_splits_resources_from_user_data(frozen):
    bundle, home = frozen
    assert rp.app_root() == str(bundle)
    assert rp.bundled_flows_dir() == os.path.join(str(bundle), "flows")
    assert rp.user_data_root() == os.path.join(str(home), rp.USER_DIR_NAME)
    assert rp.reports_dir().startswith(str(home))


def test_frozen_searches_the_users_flows_before_the_bundled_ones(frozen):
    # The order is the feature: a recorded scenario shadows a bundled one of the
    # same id, and a scenario in the user's tree can still use: the shipped blocks.
    bundle, home = frozen
    assert rp.flows_search_path() == [
        os.path.join(str(home), rp.USER_DIR_NAME, "flows"),
        os.path.join(str(bundle), "flows"),
    ]
    # What gets written is the user's tree, never the bundle.
    assert rp.flows_dir() == rp.user_flows_dir()


def test_a_checkout_has_one_flows_tree():
    # Both roles are the same directory there, so the search path collapses and a
    # checkout resolves flows exactly as it did before there was a search path.
    assert rp.flows_search_path() == [rp.bundled_flows_dir()]


def test_first_run_creates_somewhere_to_put_scenarios(frozen):
    bundle, home = frozen
    root = rp.ensure_user_data_root()
    assert os.path.isdir(os.path.join(root, "flows", "scenarios"))


def test_cms_home_overrides_everything(frozen, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    os.environ[rp.HOME_ENV] = str(elsewhere)
    try:
        assert rp.user_data_root() == str(elsewhere)
    finally:
        del os.environ[rp.HOME_ENV]


def test_first_run_creates_the_directories_and_seeds_the_config(frozen):
    bundle, home = frozen
    (bundle / "users.example.json").write_text('{"users": []}', encoding="utf-8")
    root = rp.ensure_user_data_root()

    assert os.path.isdir(os.path.join(root, "user_sessions"))
    assert os.path.isdir(os.path.join(root, "reports"))
    config = os.path.join(root, "users.json")
    assert open(config, encoding="utf-8").read() == '{"users": []}'
    # It grows real passwords, so it is never world-readable, not even briefly.
    assert oct(os.stat(config).st_mode & 0o777) == "0o600"


def test_first_run_never_overwrites_existing_user_data(frozen):
    bundle, home = frozen
    (bundle / "users.example.json").write_text("{}", encoding="utf-8")
    root = rp.ensure_user_data_root()
    config = os.path.join(root, "users.json")
    with open(config, "w", encoding="utf-8") as fh:
        fh.write('{"mine": true}')
    profile = os.path.join(root, "user_sessions", "localhost-admin")
    os.makedirs(profile)

    rp.ensure_user_data_root()   # an upgrade, or simply the next launch

    assert open(config, encoding="utf-8").read() == '{"mine": true}'
    assert os.path.isdir(profile)


# ------------------------------------------------------ handing off to Chrome

def test_clean_env_restores_the_library_path_pyinstaller_replaced(monkeypatch):
    # Chrome must not load the bundle's libssl. The bootloader stashes the real
    # value under _ORIG precisely so it can be put back for a child like this.
    monkeypatch.setattr(rp, "FROZEN", True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/app/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib/mine")
    env = rp.clean_subprocess_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/mine"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_clean_env_drops_the_library_path_when_there_was_none(monkeypatch):
    monkeypatch.setattr(rp, "FROZEN", True)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/app/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    assert "LD_LIBRARY_PATH" not in rp.clean_subprocess_env()


def test_clean_env_leaves_a_checkout_alone(monkeypatch):
    monkeypatch.setattr(rp, "FROZEN", False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/usr/lib/mine")
    assert rp.clean_subprocess_env()["LD_LIBRARY_PATH"] == "/usr/lib/mine"


# ------------------------------------------------------- what the bundle ships

def _gui_spec():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "packaging", "pyinstaller", "gui.spec"),
              encoding="utf-8") as fh:
        return fh.read()


def test_the_gui_bundle_ships_its_assets():
    """PyInstaller follows imports and nothing else.

    A file the app opens by path is simply absent from the bundle unless the
    spec names it - which is how 0.8.0 first shipped with no splash: the app
    looked for the artwork, found nothing, and started straight into the main
    window. Nothing in the app's own tests could catch that, because in a
    checkout the file is right there.
    """
    spec = _gui_spec()
    assert "datas=datas" in spec, "the spec must pass its datas to Analysis"
    assert '"cms_gui", "assets"' in spec, "assets/ must be bundled"


def test_the_assets_the_gui_reads_at_runtime_exist():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(root, "gui", "cms_gui", "assets")
    assert os.path.isdir(assets)
    # At least one splash the loader will accept, or there is nothing to bundle.
    assert any(name.startswith("splash.") for name in os.listdir(assets))
