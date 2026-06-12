"""Path-boundary-safe prefix matching. Bare startswith/LIKE 'dir%' over-match
siblings: '/media/movies2' starts with '/media/movies'."""
import os


def is_path_under(path, prefix):
    """True if path equals prefix or is underneath it (lexical comparison)."""
    if not path or not prefix:
        return False
    prefix = prefix.rstrip(os.sep) or os.sep
    if prefix == os.sep:
        return path.startswith(os.sep)
    return path == prefix or path.startswith(prefix + os.sep)


def like_prefix(directory_path):
    """SQL LIKE pattern for paths under a directory; callers must pass
    escape='\\\\' to .like()."""
    escaped = (directory_path
               .replace('\\', '\\\\')
               .replace('%', '\\%')
               .replace('_', '\\_'))
    return escaped.rstrip(os.sep) + os.sep + '%'
