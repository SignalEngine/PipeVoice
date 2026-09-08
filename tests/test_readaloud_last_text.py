

def test_keep_last_text_writes_the_ocr_output_beside_the_log(tmp_path, caplog):
    import logging

    from wisprlite import readaloud

    with caplog.at_level(logging.INFO, logger="wisprlite"):
        readaloud.keep_last_text("hello there world", tmp_path / "cfg")
    assert (tmp_path / "cfg" / "read-aloud-last.txt").read_text(encoding="utf-8") == "hello there world"
    assert any("17 chars, 3 words" in r.message for r in caplog.records)
