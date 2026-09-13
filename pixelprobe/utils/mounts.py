"""Stable mount observations for administrator-required scan roots."""

import os

from pixelprobe.utils.paths import is_path_under


def _unescape_mountinfo(value):
    for escaped, character in (('\\040', ' '), ('\\011', '\t'), ('\\012', '\n'), ('\\134', '\\')):
        value = value.replace(escaped, character)
    return value


def observe_mount(path):
    """Return the most-specific Linux mount attributes backing path, or None."""
    resolved = os.path.realpath(path)
    try:
        with open('/proc/self/mountinfo', encoding='utf-8') as mountinfo:
            entries = []
            for line in mountinfo:
                fields = line.rstrip('\n').split(' - ', 1)
                if len(fields) != 2:
                    continue
                left, right = fields
                left_fields = left.split()
                right_fields = right.split()
                if len(left_fields) < 5 or len(right_fields) < 2:
                    continue
                mount_root = _unescape_mountinfo(left_fields[3])
                mount_point = os.path.normpath(_unescape_mountinfo(left_fields[4]))
                if is_path_under(resolved, mount_point):
                    entries.append((len(mount_point), {
                        'filesystem_type': right_fields[0],
                        'source': _unescape_mountinfo(right_fields[1]),
                        'root': os.path.normpath(mount_root),
                        'mount_point': mount_point,
                    }))
    except OSError:
        return None
    if not entries:
        return None
    return max(entries, key=lambda entry: entry[0])[1]


def mount_matches_baseline(path, filesystem_type, source, root):
    """Whether the current mounted filesystem matches an approved baseline."""
    observed = observe_mount(path)
    if observed is None:
        return False, None
    expected = (filesystem_type, source, root)
    actual = (observed['filesystem_type'], observed['source'], observed['root'])
    return actual == expected, observed
