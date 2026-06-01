"""Unit tests for pixelprobe.utils.helpers."""
import errno
import os

import pytest

from pixelprobe.utils.helpers import (
    classify_path_existence,
    PATH_EXISTS,
    PATH_ABSENT,
    PATH_UNKNOWN,
)


class TestClassifyPathExistence:
    """A transient/IO error must never be reported as absent (would drive
    orphan cleanup to delete rows for files it could not verify)."""

    def test_existing_file_is_exists(self, tmp_path):
        f = tmp_path / "real.mkv"
        f.write_text("x")
        assert classify_path_existence(str(f)) == PATH_EXISTS

    def test_missing_file_is_absent(self, tmp_path):
        assert classify_path_existence(str(tmp_path / "gone.mkv")) == PATH_ABSENT

    def test_broken_symlink_is_absent(self, tmp_path):
        link = tmp_path / "link.mkv"
        os.symlink(str(tmp_path / "no-target"), str(link))
        # os.stat follows the link and raises ENOENT -> treated as a real orphan
        assert classify_path_existence(str(link)) == PATH_ABSENT

    def test_io_error_is_unknown(self, monkeypatch):
        def boom(_path):
            raise OSError(errno.EIO, "Input/output error")
        monkeypatch.setattr(os, "stat", boom)
        assert classify_path_existence("/mnt/down/file.mkv") == PATH_UNKNOWN

    def test_stale_nfs_handle_is_unknown(self, monkeypatch):
        def boom(_path):
            raise OSError(errno.ESTALE, "Stale file handle")
        monkeypatch.setattr(os, "stat", boom)
        assert classify_path_existence("/mnt/nfs/file.mkv") == PATH_UNKNOWN

    def test_permission_error_is_unknown(self, monkeypatch):
        def boom(_path):
            raise OSError(errno.EACCES, "Permission denied")
        monkeypatch.setattr(os, "stat", boom)
        assert classify_path_existence("/mnt/locked/file.mkv") == PATH_UNKNOWN
