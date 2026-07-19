from __future__ import annotations

import os
import hashlib
from typing import TYPE_CHECKING

from utils.logger_handler import logger

if TYPE_CHECKING:
    from langchain_core.documents import Document


def get_file_md5_hex(filepath: str):
    """Return a file's MD5 hex digest, or None when it cannot be read."""

    if not os.path.exists(filepath):
        logger.error("[md5] file does not exist: %s", filepath)
        return

    if not os.path.isfile(filepath):
        logger.error("[md5] path is not a file: %s", filepath)
        return

    md5_obj = hashlib.md5(usedforsecurity=False)

    chunk_size = 4096
    try:
        with open(filepath, "rb") as f:
            while chunk := f.read(chunk_size):
                md5_obj.update(chunk)
        return md5_obj.hexdigest()
    except OSError as exc:
        logger.error("[md5] failed to hash %s: %s", filepath, exc)
        return None


def listdir_with_allowed_type(path: str, allowed_types: tuple[str]):
    """Recursively return files whose suffix matches an allowed type."""
    files = []

    if not os.path.isdir(path):
        logger.error("[knowledge files] path is not a directory: %s", path)
        return tuple()

    normalized_types = tuple(
        suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        for suffix in allowed_types
    )
    for root, _, names in os.walk(path):
        for name in names:
            if name.lower().endswith(normalized_types):
                files.append(os.path.join(root, name))

    return tuple(sorted(files))


def pdf_loader(filepath: str, passwd=None) -> list[Document]:
    from langchain_community.document_loaders import PyPDFLoader

    return PyPDFLoader(filepath, passwd).load()


def txt_loader(filepath: str) -> list[Document]:
    from langchain_community.document_loaders import TextLoader

    return TextLoader(filepath, encoding="utf-8").load()
