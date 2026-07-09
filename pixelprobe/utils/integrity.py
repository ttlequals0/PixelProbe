"""Pure classification logic for the integrity check (bitrot detection).

Kept free of Celery/app imports so it is unit-testable and reusable from
both the hash task and any future callers.
"""

from datetime import datetime, timezone

# A hash mismatch counts as "mtime unchanged" (bitrot signal) only within
# this tolerance, absorbing filesystem timestamp granularity (FAT-family,
# some network mounts) and float truncation.
MTIME_EPSILON_SECONDS = 2


def classify_file_change(stored_hash, current_hash, stored_modified, current_modified,
                         mtime_trusted=False, bitrot_suspected=False,
                         bitrot_candidate_hash=None):
    """Classify a hash comparison result. Returns (change_type, changed).

    Matrix (not already flagged):
      hash match                     -> unchanged
      no stored hash                 -> no_hash (baseline)
      mismatch, untrusted baseline   -> modified (re-baselines in UTC)
      mismatch, mtime changed        -> modified (legitimate edit/replace)
      mismatch, mtime unchanged      -> bitrot_suspected (no legitimate write
                                        path alters content without mtime)

    Already flagged (bitrot_suspected=True):
      current == candidate hash      -> bitrot_stable (toward auto-expire)
      current == stored baseline     -> bitrot_self_healed (transient anomaly)
      current == neither             -> bitrot_active (rot progressing)

    Args:
        stored_modified: ISO string from the database (naive = UTC when
            mtime_trusted, unclassifiable otherwise)
        current_modified: tz-aware datetime from os.stat
    """
    if bitrot_suspected:
        if bitrot_candidate_hash and current_hash == bitrot_candidate_hash:
            return 'bitrot_stable', True
        if stored_hash and current_hash == stored_hash:
            return 'bitrot_self_healed', True
        return 'bitrot_active', True

    if not stored_hash:
        return 'no_hash', True

    if current_hash == stored_hash:
        return 'unchanged', False

    stored_mod_dt = None
    if stored_modified:
        try:
            stored_mod_dt = datetime.fromisoformat(stored_modified.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            stored_mod_dt = None
    # Trusted baselines are UTC; attach the timezone if the ISO string was naive
    if stored_mod_dt is not None and stored_mod_dt.tzinfo is None:
        stored_mod_dt = stored_mod_dt.replace(tzinfo=timezone.utc)

    mtime_unchanged = (
        mtime_trusted and stored_mod_dt is not None and current_modified is not None and
        abs((current_modified - stored_mod_dt).total_seconds()) <= MTIME_EPSILON_SECONDS
    )
    return ('bitrot_suspected' if mtime_unchanged else 'modified'), True


def apply_scan_baseline(row, file_hash, last_modified):
    """Write a scan's hash/mtime baseline onto a ScanResult row.

    Single choke point for baseline writes from scan paths: refuses to
    overwrite the baseline of a bitrot-suspected file (anti-laundering - a
    bit flip can pass decode checks, so a rescan must not adopt suspect
    content), and marks the mtime trusted (UTC) whenever a real mtime is
    written. Returns True when the baseline was written.
    """
    if row.bitrot_suspected:
        return False
    row.file_hash = file_hash
    if last_modified is not None:
        row.last_modified = last_modified
        row.mtime_baseline_utc = True
    return True


def adopt_bitrot_baseline(row, last_modified):
    """Adopt the candidate hash as the new baseline and clear the bitrot flag.

    Shared by auto-expire and the manual accept action so the two paths
    cannot drift. The detection record (bitrot_detected_date/bitrot_details)
    is preserved permanently. last_modified may be None when the caller has
    no trustworthy mtime; the row then re-baselines its mtime on the next
    hash-match integrity check.
    """
    if row.bitrot_candidate_hash:
        row.file_hash = row.bitrot_candidate_hash
    if last_modified is not None:
        row.last_modified = last_modified
        row.mtime_baseline_utc = True
    row.bitrot_suspected = False
    row.bitrot_candidate_hash = None
    row.bitrot_stable_checks = 0
