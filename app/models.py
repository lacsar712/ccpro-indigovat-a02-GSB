from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)


class Workshop(Base):
    __tablename__ = "workshops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str] = mapped_column(Text, default="")

    vats: Mapped[list["Vat"]] = relationship(back_populates="workshop")


class Vat(Base):
    __tablename__ = "vats"
    __table_args__ = (
        UniqueConstraint("workshop_id", "code", name="uniq_vat_code_per_workshop"),
    )

    STATUS_IDLE = "idle"
    STATUS_REDUCING = "reducing"
    STATUS_READY = "ready"

    id: Mapped[int] = mapped_column(primary_key=True)
    workshop_id: Mapped[int] = mapped_column(ForeignKey("workshops.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    dyeType: Mapped[str] = mapped_column(String(80))
    volumeL: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default=STATUS_IDLE)

    workshop: Mapped["Workshop"] = relationship(back_populates="vats")
    lots: Mapped[list["DipLot"]] = relationship(back_populates="vat")
    mix_orders: Mapped[list["ReductionMixOrder"]] = relationship(back_populates="vat")

    def latest_lot(self) -> Optional["DipLot"]:
        if not self.lots:
            return None
        return sorted(self.lots, key=lambda x: (x.dippedAt, x.id), reverse=True)[0]


class DipLot(Base):
    __tablename__ = "dip_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    dippedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clothMeters: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    redoxMv: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)

    vat: Mapped["Vat"] = relationship(back_populates="lots")


class ReductionMixOrder(Base):
    """还原母液兑比单：闲置缸进入「还原中」前的合格依据。"""

    __tablename__ = "reduction_mix_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 插入 flush 拿到 id 后回填，故先允许 NULL（唯一索引中 NULL 互不冲突，避免并发开单撞空串）
    code: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    issuedOn: Mapped[date] = mapped_column(Date)
    motherL: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    waterL: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    chemist: Mapped[str] = mapped_column(String(80))
    issued_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    voided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    voided_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    vat: Mapped["Vat"] = relationship(back_populates="mix_orders", foreign_keys=[vat_id])
    issuer: Mapped["User"] = relationship(foreign_keys=[issued_by_id])
    voider: Mapped[Optional["User"]] = relationship(foreign_keys=[voided_by_id])
