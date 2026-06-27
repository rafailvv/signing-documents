"""user legal consents

Revision ID: 0003_user_legal_consents
Revises: 0002_user_email
Create Date: 2026-06-27
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_user_legal_consents"
down_revision = "0002_user_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("accepted_offer_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("accepted_privacy_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("accepted_personal_data_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("accepted_ai_analysis_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("accepted_usage_rules_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("accepted_marketing_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "accepted_marketing_at")
    op.drop_column("users", "accepted_usage_rules_at")
    op.drop_column("users", "accepted_ai_analysis_at")
    op.drop_column("users", "accepted_personal_data_at")
    op.drop_column("users", "accepted_privacy_at")
    op.drop_column("users", "accepted_offer_at")
