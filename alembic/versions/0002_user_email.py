"""user email

Revision ID: 0002_user_email
Revises: 0001_auth_assets
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_user_email"
down_revision = "0001_auth_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_column("users", "email")
