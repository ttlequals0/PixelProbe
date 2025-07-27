"""Add exclusions table for storing path and extension exclusions

This migration creates the exclusions table to store path and extension
exclusions in the database instead of using JSON files, which improves
persistence in containerized environments.
"""

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone


def upgrade():
    """Create exclusions table"""
    op.create_table(
        'exclusions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('exclusion_type', sa.String(20), nullable=False),
        sa.Column('value', sa.String(500), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)),
        sa.Column('is_active', sa.Boolean(), nullable=False, default=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('exclusion_type', 'value', name='_type_value_uc')
    )
    
    # Create index for faster queries
    op.create_index('idx_exclusions_type_active', 'exclusions', ['exclusion_type', 'is_active'])


def downgrade():
    """Drop exclusions table"""
    op.drop_index('idx_exclusions_type_active', 'exclusions')
    op.drop_table('exclusions')