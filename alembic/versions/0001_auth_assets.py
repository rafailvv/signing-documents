"""auth assets

Revision ID: 0001_auth_assets
Revises:
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0001_auth_assets"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
    op.create_index(op.f("ix_users_login"), "users", ["login"], unique=True)
    op.create_table(
        "user_assets",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("signature_png", sa.LargeBinary(), nullable=True),
        sa.Column("stamp_png", sa.LargeBinary(), nullable=True),
        sa.Column("signature_filename", sa.String(length=255), nullable=True),
        sa.Column("stamp_filename", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_assets")
    op.drop_index(op.f("ix_users_login"), table_name="users")
    op.drop_index(op.f("ix_users_id"), table_name="users")
    op.drop_table("users")
