"""Unit tests for path-boundary-safe prefix helpers"""

from pixelprobe.utils.paths import is_path_under, like_prefix


class TestIsPathUnder:

    def test_child_path_matches(self):
        assert is_path_under('/media/movies/film.mkv', '/media/movies') is True

    def test_exact_prefix_matches(self):
        assert is_path_under('/media/movies', '/media/movies') is True

    def test_sibling_directory_does_not_match(self):
        assert is_path_under('/media/movies2/film.mkv', '/media/movies') is False
        assert is_path_under('/media/movies2', '/media/movies') is False

    def test_trailing_slash_on_prefix(self):
        assert is_path_under('/media/movies/film.mkv', '/media/movies/') is True
        assert is_path_under('/media/movies2/film.mkv', '/media/movies/') is False

    def test_root_prefix(self):
        assert is_path_under('/anything', '/') is True

    def test_empty_inputs(self):
        assert is_path_under('', '/media') is False
        assert is_path_under('/media/x', '') is False


class TestLikePrefix:

    def test_plain_directory(self):
        assert like_prefix('/media/movies') == '/media/movies/%'

    def test_trailing_slash_stripped(self):
        assert like_prefix('/media/movies/') == '/media/movies/%'

    def test_percent_escaped(self):
        assert like_prefix('/media/100% legit') == '/media/100\\% legit/%'

    def test_underscore_escaped(self):
        assert like_prefix('/media/tv_shows') == '/media/tv\\_shows/%'

    def test_backslash_escaped(self):
        assert like_prefix('/media/back\\slash') == '/media/back\\\\slash/%'

    def test_sibling_cannot_match(self):
        # '/media/a/%' must not match '/media/abc/file'
        pattern = like_prefix('/media/a')
        assert pattern == '/media/a/%'
        assert not '/media/abc/file'.startswith(pattern[:-1])
