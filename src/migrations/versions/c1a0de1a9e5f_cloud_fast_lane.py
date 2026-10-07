"""cloud fast lane: job lane columns, settings and usage tables

Revision ID: c1a0de1a9e5f
Revises: 3e5eebc6b3b1
Create Date: 2026-10-07 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "c1a0de1a9e5f"
down_revision = "3e5eebc6b3b1"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    conn = op.get_bind()
    rows = conn.execute(sa.text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    existing = _columns("processing_job")
    with op.batch_alter_table("processing_job", schema=None) as batch_op:
        if "lane" not in existing:
            batch_op.add_column(sa.Column("lane", sa.String(length=16), nullable=True))
        if "lane_reason" not in existing:
            batch_op.add_column(sa.Column("lane_reason", sa.Text(), nullable=True))

    tables = _tables()
    if "cloud_lane_settings" not in tables:
        op.create_table(
            "cloud_lane_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "enabled", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("base_url", sa.Text(), nullable=False),
            sa.Column("api_key", sa.Text(), nullable=True),
            sa.Column("model", sa.Text(), nullable=False),
            sa.Column("language", sa.Text(), nullable=False),
            sa.Column("usd_per_hour", sa.Float(), nullable=False),
            sa.Column(
                "monthly_cap_usd", sa.Float(), nullable=False, server_default="0"
            ),
            sa.Column("max_episode_minutes", sa.Integer(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "cloud_lane_usage" not in tables:
        op.create_table(
            "cloud_lane_usage",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("post_guid", sa.String(length=255), nullable=False),
            sa.Column("model", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("audio_seconds", sa.Float(), nullable=False),
            sa.Column("estimated_usd", sa.Float(), nullable=False),
            sa.Column("billed_seconds", sa.Float(), nullable=True),
            sa.Column("cost_usd", sa.Float(), nullable=True),
            sa.Column("usd_per_hour", sa.Float(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("settled_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_cloud_lane_usage_job_id", "cloud_lane_usage", ["job_id"])
        op.create_index(
            "ix_cloud_lane_usage_post_guid", "cloud_lane_usage", ["post_guid"]
        )
        op.create_index(
            "ix_cloud_lane_usage_created_at", "cloud_lane_usage", ["created_at"]
        )


def downgrade():
    # cloud_lane_usage and cloud_lane_settings are deliberately kept: dropping
    # the usage table would reset this month's spend if the image is upgraded
    # again, and older images ignore tables they don't know. upgrade() only
    # creates them when missing.
    existing = _columns("processing_job")
    with op.batch_alter_table("processing_job", schema=None) as batch_op:
        if "lane_reason" in existing:
            batch_op.drop_column("lane_reason")
        if "lane" in existing:
            batch_op.drop_column("lane")
