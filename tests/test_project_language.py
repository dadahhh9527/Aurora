"""Repository-wide language consistency checks."""
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
TEXT_SUFFIXES = {
    ".css",
    ".csv",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "chroma_db",
    "logs",
    "runtime",
}


def test_project_text_is_english_only():
    violations = []
    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
            ".dockerignore",
            ".env.example",
            ".gitignore",
            "Dockerfile",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        if CJK_PATTERN.search(text):
            violations.append(str(path.relative_to(PROJECT_ROOT)))
    assert not violations, f"Non-English CJK text found in: {violations}"
