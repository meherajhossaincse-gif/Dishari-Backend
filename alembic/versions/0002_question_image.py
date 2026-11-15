"""question image

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("questions") as b:
        b.add_column(sa.Column("image_path", sa.String(length=255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("questions") as b:
        b.drop_column("image_path")
