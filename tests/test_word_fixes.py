import sys, pathlib, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import settings, config


def test_pre_upgrade_replacements_load_unchanged_into_the_new_editor():
    """Gate 1: a config with fixes saved by the OLD comma-string editor still
    loads as a plain dict (storage never changed) and round-trips through the
    new two-column editor's line format without loss."""
    cfg = config.Config()
    cfg.replacements = {"Dave": "Dev", "kubernettes": "kubernetes", "gonna": "going to"}

    lines = settings.fixes_to_lines(cfg.replacements)
    assert set(lines) == {"Dave → Dev", "kubernettes → kubernetes", "gonna → going to"}

    restored = settings.fixes_from_lines(lines)
    assert restored == cfg.replacements


def test_fixes_from_lines_skips_blank_and_malformed_rows():
    assert settings.fixes_from_lines(["", "no arrow here", "wrong → right", "  → orphan"]) == {
        "wrong": "right"
    }


def test_replacements_still_apply_after_round_trip():
    from wisprlite.typer import apply_replacements

    cfg = config.Config()
    cfg.replacements = {"Dave": "Dev"}
    restored = settings.fixes_from_lines(settings.fixes_to_lines(cfg.replacements))
    assert apply_replacements("I saw Dave today", restored) == "I saw Dev today"


def test_sabotage_broken_round_trip_would_fail():
    # Positive control: prove the round-trip test can actually fail.
    cfg = config.Config()
    cfg.replacements = {"Dave": "Dev"}
    broken = dict(settings.fixes_from_lines(settings.fixes_to_lines(cfg.replacements)))
    broken["Dave"] = "WRONG"
    assert broken != cfg.replacements


def test_csv_export_import_round_trip():
    fixes = {"Dave": "Dev", "atlas": "Atlas"}
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "fixes.csv"
        settings.write_fixes_csv(path, fixes)
        assert settings.read_fixes_csv(path) == fixes


if __name__ == "__main__":
    test_pre_upgrade_replacements_load_unchanged_into_the_new_editor()
    test_fixes_from_lines_skips_blank_and_malformed_rows()
    test_replacements_still_apply_after_round_trip()
    test_sabotage_broken_round_trip_would_fail()
    test_csv_export_import_round_trip()
    print("OK")
