"""Unit tests for CLI-argument safety helpers in utils.security"""

import pytest

from pixelprobe.utils.security import ensure_cli_safe_path, validate_command_args


class TestEnsureCliSafePath:

    def test_absolute_path_unchanged(self):
        assert ensure_cli_safe_path('/media/movies/film.mkv') == '/media/movies/film.mkv'

    def test_absolute_path_with_dash_segment_unchanged(self):
        assert ensure_cli_safe_path('/media/-i/file.mkv') == '/media/-i/file.mkv'

    def test_relative_dash_path_gets_dot_slash(self):
        assert ensure_cli_safe_path('-i') == './-i'
        assert ensure_cli_safe_path('-regard-warnings') == './-regard-warnings'

    def test_relative_plain_path_gets_dot_slash(self):
        assert ensure_cli_safe_path('file.mkv') == './file.mkv'


class TestValidateCommandArgs:

    def test_rejects_non_list(self):
        with pytest.raises(ValueError):
            validate_command_args('ffprobe file.mkv')

    def test_rejects_null_byte(self):
        with pytest.raises(ValueError):
            validate_command_args(['ffprobe', 'file\x00.mkv'])

    def test_rejects_newline(self):
        with pytest.raises(ValueError):
            validate_command_args(['ffprobe', 'file\n.mkv'])

    def test_allows_shell_metachars_in_filenames(self):
        # Inert with shell=False; real media filenames contain these
        args = ['ffprobe', '/media/pipe|name.mkv', '/media/tick`name.mkv',
                '/media/$amount %20 ~tmp.mkv']
        assert validate_command_args(args) == args
