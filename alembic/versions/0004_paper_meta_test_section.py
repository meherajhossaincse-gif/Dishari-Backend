"""paper print header + test section

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as b:
        b.add_column(sa.Column("meta", sa.JSON(), nullable=True))
    with op.batch_alter_table("tests") as b:
        b.add_column(sa.Column("section", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tests") as b:
        b.drop_column("section")
    with op.batch_alter_table("papers") as b:
        b.drop_column("meta")
