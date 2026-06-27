from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    login: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    accepted_offer_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_privacy_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_personal_data_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_ai_analysis_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_usage_rules_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_marketing_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assets: Mapped["UserAssets | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UserAssets(Base):
    __tablename__ = "user_assets"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    signature_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    stamp_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    signature_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stamp_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)
    user: Mapped[User] = relationship(back_populates="assets")
