"""Scoping for the mark-as-good override.

Marking a file good is a judgement about one finding on one version of the
file, made by a person who looked at the evidence. It is not a promise that
the file will be fine forever. These helpers decide whether a stored override
still applies to a fresh scan result:

- The content changed (hash differs): this is not the file that was reviewed,
  so the override lapses and the new verdict stands.
- A different class of finding appeared: the person excused a freeze warning,
  not a missing-content verdict, so the override lapses.
- Same content, same class of finding: the override holds.

Kept free of Flask and Celery imports so both the web process and the scan
workers can use it, and it stays unit-testable.
"""

# Every matching class is collected; order carries no precedence. Markers must
# match the strings the scanner actually emits (see media_checker.py) - a
# marker that matches nothing quietly turns its findings into 'other'.
_FINDING_MARKERS = (
    ('incomplete', ('incomplete file',)),
    ('missing-content', ('missing content',)),
    ('decode', ('decode failure', 'decoding errors', 'hevc reference picture',
                'ffmpeg errors')),
    ('freeze', ('video freeze warning',)),
    ('frame-count', ('count differs',)),
    ('temporal', ('temporal outlier', 'vertical line repetition', 'tout')),
    ('timestamp', ('timestamps detected', 'timestamp inconsistencies',
                   'timestamp warning')),
)

# The mixed-cause freeze summary names both underlying causes.
_COMPOUND_MARKERS = (
    ('damaged video', ('missing-content', 'decode')),
)


def classify_findings(*detail_texts):
    """The set of finding classes present in corruption/warning detail text.

    Returns a sorted tuple of class names; unrecognised findings map to
    'other' so they can never hide behind an override scoped to something
    else.
    """
    classes = set()
    for text in detail_texts:
        if not text:
            continue
        # Details are stored '; '-joined; classify each finding on its own so
        # an unrecognised one cannot hide behind a recognised neighbour.
        for fragment in text.split('; '):
            fragment = fragment.strip()
            if not fragment:
                continue
            lowered = fragment.lower()
            matched = False
            for name, compound in _COMPOUND_MARKERS:
                if name in lowered:
                    classes.update(compound)
                    matched = True
            for name, markers in _FINDING_MARKERS:
                if any(marker in lowered for marker in markers):
                    classes.add(name)
                    matched = True
            if not matched:
                classes.add('other')
    return tuple(sorted(classes))


def encode_verdict(classes):
    """Store a class set as the comma-joined string the model column holds."""
    return ','.join(classes) if classes else None


def override_still_applies(marked_good_hash, marked_good_verdict,
                           current_hash, corruption_details, warning_details):
    """Whether a stored override covers this fresh scan result.

    A missing stored hash keeps legacy behaviour (content changes cannot be
    detected, so only the class check applies). A NULL stored verdict excuses
    every class, again for legacy rows marked before scoping existed.
    """
    if marked_good_hash and current_hash and marked_good_hash != current_hash:
        return False

    if marked_good_verdict is None:
        # A NULL stored verdict excuses everything (rows marked before scoping)
        return True

    excused = set(marked_good_verdict.split(','))
    found = set(classify_findings(corruption_details, warning_details))
    return found <= excused


def retire_stale_override(db_result, current_hash, corruption_details, warning_details):
    """Clear marked_as_good on a row whose override no longer covers the result.

    Mutates the row and returns True when the override was retired. The
    history columns (hash, date, verdict) stay set. Call from every path that
    persists a fresh scan result, or a single-file rescan will keep reporting
    a damaged file as healthy.
    """
    if not db_result.marked_as_good:
        return False
    if not (corruption_details or warning_details):
        return False
    if override_still_applies(db_result.marked_good_hash,
                              db_result.marked_good_verdict,
                              current_hash, corruption_details, warning_details):
        return False
    db_result.marked_as_good = False
    return True
