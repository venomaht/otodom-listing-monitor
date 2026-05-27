from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(50), default="running")
    listings_found: Mapped[int] = mapped_column(Integer, default=0)
    new_listings: Mapped[int] = mapped_column(Integer, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, default=0)
    removed_listings: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class Listing(Base):
    __tablename__ = "listings"

    __table_args__ = (
        UniqueConstraint("city", "external_id", name="uq_city_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_per_m2: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    currency: Mapped[str] = mapped_column(String(10), default="PLN")

    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    area: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(50), default="otodom")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    snapshots: Mapped[list["ListingSnapshot"]] = relationship(back_populates="listing")
    events: Mapped[list["ListingEvent"]] = relationship(back_populates="listing")


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    scrape_run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"), nullable=False)

    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_per_m2: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    currency: Mapped[str] = mapped_column(String(10), default="PLN")

    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    area: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    rooms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    listing: Mapped["Listing"] = relationship(back_populates="snapshots")


class ListingEvent(Base):
    __tablename__ = "listing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"), nullable=False)
    scrape_run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id"), nullable=False)

    event_type: Mapped[str] = mapped_column(String(50), nullable=False)

    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    listing: Mapped["Listing"] = relationship(back_populates="events")