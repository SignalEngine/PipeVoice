"""The Clear-cache button: what it removes, and what it must never remove."""

import os
import pathlib
import sys
import tempfile

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from wisprlite import modelcache


def _make_cache(tmp):
    root = pathlib.Path(tmp) / "hub"
    (root / "models--Systran--faster-whisper-base.en" / "blobs").mkdir(parents=True)
    (root / "models--Systran--faster-whisper-base.en" / "blobs" / "model.bin").write_bytes(b"x" * 3000)
    (root / "models--guillaumekln--faster-whisper-tiny").mkdir(parents=True)
    (root / "models--guillaumekln--faster-whisper-tiny" / "m.bin").write_bytes(b"y" * 1000)
    # Somebody else's model. This is a SHARED cache.
    (root / "models--meta-llama--Llama-3-8B").mkdir(parents=True)
    (root / "models--meta-llama--Llama-3-8B" / "weights.bin").write_bytes(b"z" * 5000)
    return root


def test_it_only_ever_touches_faster_whisper_models(monkeypatch):
    """The HuggingFace cache is shared with anything else the user runs. Taking
    the whole folder would delete someone's unrelated 8B model."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_cache(tmp)
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(root))

        names = [p.name for p in modelcache.model_dirs()]
        assert len(names) == 2, names
        assert all("faster-whisper" in n for n in names)

        ok, message = modelcache.clear()

        assert ok, message
        assert not (root / "models--Systran--faster-whisper-base.en").exists()
        assert not (root / "models--guillaumekln--faster-whisper-tiny").exists()
        assert (root / "models--meta-llama--Llama-3-8B" / "weights.bin").read_bytes() == b"z" * 5000, \
            "it deleted somebody else's model"


def test_the_size_it_reports_covers_only_our_models(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_cache(tmp)
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(root))
        assert modelcache.size_bytes() == 4000, "the 5000-byte Llama was counted"


def test_an_empty_cache_is_not_an_error(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(pathlib.Path(tmp) / "nope"))
        assert modelcache.model_dirs() == []
        assert modelcache.size_bytes() == 0
        ok, message = modelcache.clear()
        assert ok and "Nothing cached" in message


def test_a_locked_model_is_reported_not_glossed_over(monkeypatch):
    """"Cleared" while the broken download is still there is the worst possible
    answer: the user retries, it is still slow, and they stop trusting it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = _make_cache(tmp)
        monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", str(root))
        real_rmtree = modelcache.shutil.rmtree

        def refuse_one(path, *a, **kw):
            if "base.en" in str(path):
                raise OSError(32, "The process cannot access the file")
            return real_rmtree(path, *a, **kw)

        monkeypatch.setattr(modelcache.shutil, "rmtree", refuse_one)
        ok, message = modelcache.clear()

        assert not ok, "a partial failure must not report success"
        assert "Close PipeVoice" in message
        assert (root / "models--Systran--faster-whisper-base.en").exists()


def test_it_finds_the_cache_the_way_the_library_does(monkeypatch):
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.setenv("HUGGINGFACE_HUB_CACHE", "/explicit/hub")
    assert modelcache.cache_dir() == pathlib.Path("/explicit/hub")

    monkeypatch.delenv("HUGGINGFACE_HUB_CACHE")
    monkeypatch.setenv("HF_HOME", "/somewhere/hf")
    assert modelcache.cache_dir() == pathlib.Path("/somewhere/hf/hub")

    monkeypatch.delenv("HF_HOME")
    assert modelcache.cache_dir().parts[-3:] == (".cache", "huggingface", "hub")
