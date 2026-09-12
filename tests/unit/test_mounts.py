from unittest.mock import mock_open, patch

import pytest

from pixelprobe.utils.mounts import mount_matches_baseline, observe_mount


def _mountinfo(filesystem_type='nfs', source='server:/library', root='/library'):
    return (
        '21 1 0:1 / / rw,relatime - ext4 /dev/root rw\n'
        f'99 21 0:42 {root} /media rw,relatime - {filesystem_type} {source} rw\n'
    )


def test_observe_mount_uses_stable_attributes_not_mount_id():
    with patch('builtins.open', mock_open(read_data=_mountinfo())):
        observed = observe_mount('/media/movies/example.mkv')

    assert observed == {
        'filesystem_type': 'nfs',
        'source': 'server:/library',
        'root': '/library',
        'mount_point': '/media',
    }


@pytest.mark.parametrize(
    ('filesystem_type', 'source', 'root'),
    [
        ('cifs', 'server:/library', '/library'),
        ('nfs', 'other-server:/library', '/library'),
        ('nfs', 'server:/library', '/different-root'),
    ],
)
def test_mount_baseline_rejects_changed_stable_attribute(filesystem_type, source, root):
    with patch('builtins.open', mock_open(read_data=_mountinfo(filesystem_type, source, root))):
        matched, observed = mount_matches_baseline(
            '/media/movies/example.mkv', 'nfs', 'server:/library', '/library')

    assert not matched
    assert observed['filesystem_type'] == filesystem_type
    assert observed['source'] == source
    assert observed['root'] == root
