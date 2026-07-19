"""Offline unit tests for utility functions."""
import hashlib
import os

from utils.file_handler import listdir_with_allowed_type, get_file_md5_hex
from utils.config_handler import _resolve_env
from utils.content import content_to_text


class TestListdirWithAllowedType:
    def test_non_dir_returns_empty_tuple(self):
        # A missing directory must return an empty result.
        assert listdir_with_allowed_type("no_such_dir_xxx", (".txt",)) == tuple()

    def test_filters_by_suffix(self, tmp_path):
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.pdf").write_text("x", encoding="utf-8")
        (tmp_path / "c.md").write_text("x", encoding="utf-8")

        result = listdir_with_allowed_type(str(tmp_path), ("txt", "pdf"))
        names = sorted(os.path.basename(p) for p in result)
        assert names == ["a.txt", "b.pdf"]

    def test_scans_nested_knowledge_directories(self, tmp_path):
        nested = tmp_path / "manuals" / "model-x"
        nested.mkdir(parents=True)
        (nested / "guide.TXT").write_text("x", encoding="utf-8")
        result = listdir_with_allowed_type(str(tmp_path), ("txt", "pdf"))
        assert result == (str(nested / "guide.TXT"),)


class TestFileMd5:
    def test_md5_matches_hashlib(self, tmp_path):
        f = tmp_path / "x.txt"
        content = b"hello aurora"
        f.write_bytes(content)
        assert get_file_md5_hex(str(f)) == hashlib.md5(content).hexdigest()

    def test_missing_file_returns_none(self):
        assert get_file_md5_hex("definitely_missing_file.txt") is None


class TestResolveEnv:
    def test_substitutes_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        assert _resolve_env("${MY_TOKEN}") == "secret123"

    def test_uses_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _resolve_env("${MISSING_VAR:-fallback}") == "fallback"

    def test_recurses_dict_and_list(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        data = {"url": "https://${HOST}/api", "items": ["${HOST}", "plain"]}
        assert _resolve_env(data) == {
            "url": "https://example.com/api",
            "items": ["example.com", "plain"],
        }


def test_content_to_text_normalizes_multipart_messages():
    assert content_to_text(
        [{"type": "text", "text": "hello"}, " ", {"text": "world"}]
    ) == "hello world"
