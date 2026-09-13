import csv
import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / 'tools' / 'delete_files_from_csv.sh'


def write_export(path, filenames):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['ID', 'File Path', 'Scan Status'])
        writer.writeheader()
        for index, filename in enumerate(filenames, 1):
            writer.writerow({'ID': index, 'File Path': filename, 'Scan Status': 'completed'})


def run_script(csv_path, root, *args):
    env = {**os.environ, 'SCAN_PATHS': str(root)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(csv_path), *args],
        text=True, capture_output=True, env=env, check=False,
    )


def test_csv_parser_handles_quotes_commas_newlines_and_leading_hyphens(tmp_path):
    filenames = [
        str(tmp_path / 'comma,name.txt'),
        str(tmp_path / 'line\nfeed.txt'),
        str(tmp_path / '-leading-hyphen.txt'),
    ]
    for filename in filenames:
        Path(filename).touch()
    csv_path = tmp_path / 'export.csv'
    write_export(csv_path, filenames)

    result = run_script(csv_path, tmp_path)

    assert result.returncode == 0
    assert 'DRY RUN' in result.stdout
    assert all(Path(filename).exists() for filename in filenames)


def test_csv_apply_requires_explicit_flag_and_rejects_neighbor(tmp_path):
    target = tmp_path / 'target.txt'
    neighbor = tmp_path / 'target.txt.bak'
    outside = tmp_path.parent / 'outside-delete-test.txt'
    target.touch()
    neighbor.touch()
    outside.touch()
    csv_path = tmp_path / 'export.csv'
    write_export(csv_path, [str(target), str(outside)])

    dry_run = run_script(csv_path, tmp_path)
    assert dry_run.returncode == 0
    assert target.exists() and neighbor.exists() and outside.exists()

    applied = run_script(csv_path, tmp_path, '--apply', '--no-confirm')
    assert applied.returncode == 0
    assert not target.exists()
    assert neighbor.exists()
    assert outside.exists()


def test_csv_apply_rejects_final_and_ancestor_symlink_replacements(tmp_path):
    outside = tmp_path.parent / 'outside-delete-target.txt'
    outside.write_text('outside')
    final_link = tmp_path / 'final-link.txt'
    final_link.symlink_to(outside)
    nested = tmp_path / 'nested'
    nested.mkdir()
    ancestor_link = tmp_path / 'ancestor'
    ancestor_link.symlink_to(nested, target_is_directory=True)
    nested_target = nested / 'target.txt'
    nested_target.write_text('inside')
    csv_path = tmp_path / 'export.csv'
    write_export(csv_path, [str(final_link), str(ancestor_link / 'target.txt')])

    result = run_script(csv_path, tmp_path, '--apply', '--no-confirm')

    assert result.returncode == 0
    assert outside.exists()
    assert nested_target.exists()
    assert 'found 0 files' in result.stdout
