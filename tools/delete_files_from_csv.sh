#!/usr/bin/env python3
"""Safely remove files listed in a PixelProbe CSV export."""
import argparse
import csv
import os
import stat
import sys
from pathlib import Path


def display_path(path):
    return repr(path)


def safe_relative_path(path, roots):
    candidate = os.path.normpath(os.path.abspath(path))
    for root in roots:
        try:
            relative = os.path.relpath(candidate, root)
        except ValueError:
            continue
        if relative != os.pardir and not relative.startswith(os.pardir + os.sep):
            return root, relative
    return None, None


def open_directory_no_follow(path):
    if not hasattr(os, 'O_DIRECTORY') or not hasattr(os, 'O_NOFOLLOW'):
        raise OSError('secure descriptor traversal is unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    fd = os.open(os.path.sep, flags)
    try:
        for component in filter(None, path.strip(os.path.sep).split(os.path.sep)):
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
        os.close(fd)
        raise


def unlink_under_root(root, relative):
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, 'O_CLOEXEC', 0)
    directory_fd = open_directory_no_follow(root)
    try:
        parts = relative.split(os.sep)
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        final_stat = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(final_stat.st_mode):
            raise OSError('target is not a regular file')
        os.unlink(parts[-1], dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_file')
    parser.add_argument('--root', action='append', default=[], help='Allowed root, repeatable')
    parser.add_argument('--apply', action='store_true', help='Actually delete files')
    parser.add_argument('--no-confirm', action='store_true', help='Skip confirmation')
    args = parser.parse_args()
    csv_path = Path(args.csv_file)
    if not csv_path.is_file():
        print(f'Error: CSV file {csv_path} not found', file=sys.stderr)
        return 1
    roots = [root for root in args.root if root]
    if not roots:
        roots = [root for root in os.environ.get('SCAN_PATHS', '').split(',') if root]
    roots = [os.path.normpath(os.path.abspath(root)) for root in roots]
    if not roots:
        print('Error: provide --root or configure SCAN_PATHS', file=sys.stderr)
        return 1
    paths = []
    with csv_path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        path_column = next((name for name in ('File Path', 'FilePath') if name in (reader.fieldnames or [])), None)
        if not path_column:
            print('Error: CSV must contain a File Path or FilePath column', file=sys.stderr)
            return 1
        for row_number, row in enumerate(reader, 2):
            raw_path = row.get(path_column) or ''
            if not raw_path:
                print(f'Row {row_number}: empty File Path, skipped', file=sys.stderr)
                continue
            root, relative = safe_relative_path(raw_path, roots)
            if root is None:
                print(f'Row {row_number}: outside allowed roots, skipped: {display_path(raw_path)}', file=sys.stderr)
                continue
            paths.append((root, relative, raw_path))
    paths = list(dict.fromkeys((root, relative) for root, relative, _ in paths))
    existing = []
    for root, relative in paths:
        try:
            directory_fd = open_directory_no_follow(root)
            try:
                parts = relative.split(os.sep)
                for part in parts[:-1]:
                    next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=directory_fd)
                    os.close(directory_fd)
                    directory_fd = next_fd
                target_stat = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISREG(target_stat.st_mode):
                    existing.append((root, relative))
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    missing = len(paths) - len(existing)
    print(f'Validated {len(paths)} entries; found {len(existing)} files; missing {missing}')
    if not args.apply:
        print('DRY RUN: no files will be deleted')
        for root, relative in existing:
            print(f'[DRY RUN] Would delete: {display_path(os.path.join(root, relative))}')
        return 0
    if not args.no_confirm:
        if input(f'Delete {len(existing)} files? Type yes to continue: ') != 'yes':
            print('Operation cancelled')
            return 0
    deleted = failed = 0
    for root, relative in existing:
        path = os.path.join(root, relative)
        try:
            unlink_under_root(root, relative)
            deleted += 1
            print(f'Deleted: {display_path(path)}')
        except OSError as error:
            failed += 1
            print(f'Failed to delete {display_path(path)}: {error}', file=sys.stderr)
    print(f'Deleted {deleted} files; failed {failed}; missing {missing}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
