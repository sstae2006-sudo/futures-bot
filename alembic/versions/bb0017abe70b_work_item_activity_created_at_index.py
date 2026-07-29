"""work_item_activity.created_at index (SIL Phase 6 audit)

Revision ID: bb0017abe70b
Revises: 7a3f9c2e1b0d
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bb0017abe70b'
down_revision: Union[str, Sequence[str], None] = '7a3f9c2e1b0d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # SIL Phase 6 audit finding: GET /api/activity/timeline's global path
    # (fetch_activity(work_item_id=None)) filters on nothing but sorts by
    # created_at DESC -- the existing idx_work_item_activity_item
    # composite index (work_item_id, created_at) only helps once
    # work_item_id is constrained, so this was a full table scan + sort.
    # Purely additive -- a new index, no column/behavior change.
    op.create_index('idx_work_item_activity_created_at', 'work_item_activity', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_work_item_activity_created_at', table_name='work_item_activity')
