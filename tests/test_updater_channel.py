"""The staging ring: a prerelease reaches beta installs only.

A mistake in here silently ends auto-updates for every existing install, so the
STABLE path is asserted to be untouched, not just assumed.
"""

import json
import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import updater

LATEST = {
    "tag_name": "v2.32.1", "draft": False, "prerelease": False,
    "assets": [{"name": "Pipevoice-Setup.exe",
                "browser_download_url": "http://example/stable.exe"}],
}
ALL = [
    {"tag_name": "v2.33.0", "draft": False, "prerelease": True,
     "assets": [{"name": "Pipevoice-Setup.exe",
                 "browser_download_url": "http://example/beta.exe"}]},
    LATEST,
    {"tag_name": "v2.30.0", "draft": False, "prerelease": False, "assets": []},
    # A draft must never be offered to anyone.
    {"tag_name": "v9.9.9", "draft": True, "prerelease": True,
     "assets": [{"name": "Pipevoice-Setup.exe",
                 "browser_download_url": "http://example/draft.exe"}]},
]


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _install(monkey_channel, urls):
    def fake_urlopen(req, timeout=10):
        url = getattr(req, "full_url", str(req))
        urls.append(url)
        return _Resp(ALL if "per_page" in url else LATEST)

    updater.urllib.request.urlopen = fake_urlopen
    updater.config.Config.load = classmethod(
        lambda cls: types.SimpleNamespace(update_channel=monkey_channel)
    )


def _check_as(version, channel):
    urls = []
    _install(channel, urls)
    original = updater.__version__
    updater.__version__ = version
    try:
        result = updater.check()
    finally:
        updater.__version__ = original
    return result, urls


def test_stable_never_sees_a_prerelease():
    result, urls = _check_as("2.32.0", "stable")
    assert result["tag"] == "v2.32.1", "stable must get the promoted release"
    assert all("per_page" not in u for u in urls), (
        "stable must only ever hit /releases/latest — that endpoint excludes "
        "prereleases, which is the entire mechanism"
    )


def test_beta_sees_the_prerelease_first():
    result, _urls = _check_as("2.32.0", "beta")
    assert result["tag"] == "v2.33.0"
    assert "beta.exe" in result["url"]


def test_nobody_is_offered_a_draft():
    # A draft is an unfinished release; offering it would push a half-built
    # installer to whoever opted into beta.
    result, _urls = _check_as("2.32.0", "beta")
    assert result["tag"] != "v9.9.9"


def test_up_to_date_installs_are_offered_nothing():
    assert _check_as("2.32.1", "stable")[0] is None
    assert _check_as("2.33.0", "beta")[0] is None


def test_an_unreadable_config_still_updates_on_stable():
    # A broken config must never strand someone without updates.
    urls = []

    def fake_urlopen(req, timeout=10):
        urls.append(getattr(req, "full_url", str(req)))
        return _Resp(LATEST)

    updater.urllib.request.urlopen = fake_urlopen

    def explode(cls):
        raise RuntimeError("config.json is corrupt")

    updater.config.Config.load = classmethod(explode)
    original = updater.__version__
    updater.__version__ = "2.32.0"
    try:
        result = updater.check()
    finally:
        updater.__version__ = original
    assert result["tag"] == "v2.32.1"
    assert all("per_page" not in u for u in urls)
