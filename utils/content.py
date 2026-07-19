"""Helpers for normalizing model message content."""


def content_to_text(content) -> str:
    """Normalize string or multipart model content to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content) if content is not None else ""
