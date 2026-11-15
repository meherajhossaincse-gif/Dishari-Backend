"""profiles, parent link codes, online tests

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("phone", sa.String(length=30), nullable=True))
        b.add_column(sa.Column("avatar_path", sa.String(length=255), nullable=True))
        b.add_column(sa.Column("profile", sa.JSON(), nullable=True))
        b.add_column(sa.Column("link_code", sa.String(length=12), nullable=True))
        b.create_unique_constraint("uq_users_link_code", ["link_code"])
    op.create_table(
        "tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("paper_id", sa.Integer(), sa.ForeignKey("papers.id"), nullable=False, index=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("class_level", sa.String(length=20), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_min", sa.Integer(), nullable=False),
        sa.Column("shuffle", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("show_answers", sa.String(length=15), nullable=False, server_default="after_end"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "test_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("tests.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("student_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("question_order", sa.JSON(), nullable=False),
        sa.Column("answers", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("late", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("breakdown", sa.JSON(), nullable=False),
        sa.UniqueConstraint("test_id", "student_id"),
    )


def downgrade() -> None:
    op.drop_table("test_attempts")
    op.drop_table("tests")
    with op.batch_alter_table("users") as b:
        b.drop_constraint("uq_users_link_code", type_="unique")
        for c in ("link_code", "profile", "avatar_path", "phone"):
            b.drop_column(c)
