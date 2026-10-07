"""stage pipeline: processing_job.stage and processing_job.priority

Revision ID: d7e3f1a2b4c5
Revises: c1a0de1a9e5f
Create Date: 2026-10-07 18:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "d7e3f1a2b4c5"
down_revision = "c1a0de1a9e5f"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    conn = op.get_bind()
    rows = conn.execute(sa.text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def upgrade():
    existing = _columns("processing_job")
    with op.batch_alter_table("processing_job", schema=None) as batch_op:
        if "stage" not in existing:
            batch_op.add_column(sa.Column("stage", sa.String(length=16), nullable=True))
        if "priority" not in existing:
            batch_op.add_column(
                sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
            )


def downgrade():
    existing = _columns("processing_job")
    with op.batch_alter_table("processing_job", schema=None) as batch_op:
        if "priority" in existing:
            batch_op.drop_column("priority")
        if "stage" in existing:
            batch_op.drop_column("stage")
