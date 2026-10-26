import hashlib
from datetime import datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy import Boolean, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('owner', 'admin')", name="role_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default=ROLE_OWNER)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # --- passwords -------------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    # --- roles -----------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    # --- Flask-Login -----------------------------------------------------
    def _fingerprint(self) -> str:
        return hashlib.sha256(self.password_hash.encode()).hexdigest()[:16]

    def get_id(self) -> str:
        # Embedding a password fingerprint invalidates every session when the password changes.
        return f"{self.id}.{self._fingerprint()}"

    def __repr__(self) -> str:
        return f"<User {self.id} {self.role}>"
