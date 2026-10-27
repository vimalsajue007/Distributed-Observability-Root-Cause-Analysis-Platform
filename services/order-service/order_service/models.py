from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from obs_common.db import BigPK, UTCDateTime, make_base
from obs_common.timeutil import utcnow

Base = make_base()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (Index("ix_orders_created", "created_at"), Index("ix_orders_customer", "customer_id"))

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # pending | completed | payment_failed | failed
    payment_id: Mapped[int | None] = mapped_column(BigPK)
    notification_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")  # pending | sent | failed | skipped
    scenario: Mapped[str] = mapped_column(String(40), nullable=False, default="normal")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigPK, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
