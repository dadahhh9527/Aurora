"""Project-relative path helpers."""
import os


def get_project_root() -> str:
    """Return the absolute project root directory."""
    current_file = os.path.abspath(__file__)
    current_dir = os.path.dirname(current_file)
    return os.path.dirname(current_dir)


def get_abs_path(relative_path: str) -> str:
    """Resolve a path relative to the project root."""
    if os.path.isabs(relative_path):
        return os.path.normpath(relative_path)
    return os.path.normpath(os.path.join(get_project_root(), relative_path))
