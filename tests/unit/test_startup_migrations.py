"""Startup migrations must actually reach the tables they name.

v2.8.9 shipped an ALTER against 'cleanup_states'; the table is 'cleanup_state'.
The migration swallowed the error, the column was never added, and every query
for a column the model declares failed against a database without it.
"""

import re

from pixelprobe.migrations import startup
from pixelprobe.models import db


def test_every_migrated_table_exists(app):
    """A table named in a migration has to be one the models define."""
    with app.app_context():
        known = set(db.metadata.tables)

    source = (startup.__file__).replace('.pyc', '.py')
    with open(source) as handle:
        text = handle.read()

    named = set(re.findall(r'ALTER TABLE ([a-z_]+) ', text))
    unknown = named - known
    assert not unknown, f'migrations name tables that do not exist: {sorted(unknown)}'
