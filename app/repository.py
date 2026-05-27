
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Listing, ListingEvent, ListingSnapshot, ScrapeRun
from app.scraper import validate_removed_candidates


EVENT_NEWLY_FOUND = "newly_found"
EVENT_NEW_OFFER = "new_offer"
EVENT_PRICE_CHANGE = "price_change"
EVENT_REMOVED = "removed"


def create_scrape_run(db: Session, city: str) -> ScrapeRun:
    scrape_run = ScrapeRun(
        city=city,
        status="running",
        started_at=datetime.utcnow(),
    )

    db.add(scrape_run)
    db.commit()
    db.refresh(scrape_run)

    return scrape_run


def finish_scrape_run(
    db: Session,
    scrape_run: ScrapeRun,
    status: str,
    listings_found: int = 0,
    new_listings: int = 0,
    price_changes: int = 0,
    removed_listings: int = 0,
    error_message: str | None = None,
) -> ScrapeRun:
    scrape_run.status = status
    scrape_run.finished_at = datetime.utcnow()
    scrape_run.listings_found = listings_found
    scrape_run.new_listings = new_listings
    scrape_run.price_changes = price_changes
    scrape_run.removed_listings = removed_listings
    scrape_run.error_message = error_message

    db.commit()
    db.refresh(scrape_run)

    return scrape_run


def create_listing_snapshot(
    db: Session,
    listing: Listing,
    scrape_run: ScrapeRun,
    data: dict,
) -> ListingSnapshot:
    snapshot = ListingSnapshot(
        listing_id=listing.id,
        scrape_run_id=scrape_run.id,
        city=scrape_run.city,
        title=data.get("title"),
        price=data.get("price"),
        price_per_m2=data.get("price_per_m2"),
        currency="PLN",
        location=data.get("location"),
        area=data.get("area"),
        rooms=data.get("rooms"),
        scraped_at=datetime.utcnow(),
    )

    db.add(snapshot)

    return snapshot


def create_listing_event(
    db: Session,
    listing: Listing,
    scrape_run: ScrapeRun,
    event_type: str,
    old_value: str | None = None,
    new_value: str | None = None,
) -> ListingEvent:
    event = ListingEvent(
        listing_id=listing.id,
        scrape_run_id=scrape_run.id,
        event_type=event_type,
        old_value=old_value,
        new_value=new_value,
        created_at=datetime.utcnow(),
    )

    db.add(event)

    return event


def create_or_update_listing_from_data(
    db: Session,
    scrape_run: ScrapeRun,
    city: str,
    data: dict,
    new_listing_event_type: str,
) -> tuple[Listing | None, bool, bool]:
    """
    Zwraca:
    - listing albo None
    - czy listing był nowy dla naszej bazy
    - czy wykryto zmianę ceny
    """

    external_id = data.get("external_id")

    if not external_id:
        return None, False, False

    existing_listing = (
        db.query(Listing)
        .filter(
            Listing.city == city,
            Listing.external_id == external_id,
        )
        .first()
    )

    was_new = False
    price_changed = False

    if existing_listing is None:
        listing = Listing(
            city=city,
            external_id=external_id,
            url=data.get("url"),
            title=data.get("title"),
            price=data.get("price"),
            price_per_m2=data.get("price_per_m2"),
            currency="PLN",
            location=data.get("location"),
            area=data.get("area"),
            rooms=data.get("rooms"),
            source="otodom",
            is_active=True,
            first_seen_at=datetime.utcnow(),
            last_seen_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )

        db.add(listing)
        db.flush()

        create_listing_event(
            db=db,
            listing=listing,
            scrape_run=scrape_run,
            event_type=new_listing_event_type,
            old_value=None,
            new_value=external_id,
        )

        was_new = True

    else:
        listing = existing_listing

        old_price = listing.price
        new_price = data.get("price")

        if old_price is not None and new_price is not None:
            if float(old_price) != float(new_price):
                create_listing_event(
                    db=db,
                    listing=listing,
                    scrape_run=scrape_run,
                    event_type=EVENT_PRICE_CHANGE,
                    old_value=str(old_price),
                    new_value=str(new_price),
                )

                price_changed = True

        listing.url = data.get("url")
        listing.title = data.get("title")
        listing.price = data.get("price")
        listing.price_per_m2 = data.get("price_per_m2")
        listing.location = data.get("location")
        listing.area = data.get("area")
        listing.rooms = data.get("rooms")
        listing.is_active = True
        listing.last_seen_at = datetime.utcnow()
        listing.updated_at = datetime.utcnow()

    create_listing_snapshot(
        db=db,
        listing=listing,
        scrape_run=scrape_run,
        data=data,
    )

    return listing, was_new, price_changed


def save_scraped_listings(
    db: Session,
    city: str,
    listings: list[dict],
) -> ScrapeRun:
    """
    Pełny scrape miasta.

    Nowe dla naszej bazy oferty zapisujemy jako:
    - newly_found

    To NIE oznacza jeszcze, że oferta została dodana na Otodom w ostatnich 24h.
    Oznacza tylko, że nasz system zobaczył ją pierwszy raz.
    """

    scrape_run = create_scrape_run(db, city)

    new_listings_count = 0
    price_changes_count = 0
    removed_listings_count = 0

    current_external_ids = {
        listing["external_id"]
        for listing in listings
        if listing.get("external_id")
    }

    try:
        for data in listings:
            _, was_new, price_changed = create_or_update_listing_from_data(
                db=db,
                scrape_run=scrape_run,
                city=city,
                data=data,
                new_listing_event_type=EVENT_NEWLY_FOUND,
            )

            if was_new:
                new_listings_count += 1

            if price_changed:
                price_changes_count += 1

        active_listings_for_city = (
            db.query(Listing)
            .filter(
                Listing.city == city,
                Listing.is_active.is_(True),
            )
            .all()
        )

        removed_candidates = []

        for listing in active_listings_for_city:
            if listing.external_id not in current_external_ids:
                removed_candidates.append(
                    {
                        "external_id": listing.external_id,
                        "url": listing.url,
                    }
                )

        print(
            f"Removed candidates before detail validation: {len(removed_candidates)}",
            flush=True,
        )

        confirmed_removed_ids = validate_removed_candidates(removed_candidates)

        print(
            f"Confirmed removed after detail validation: {len(confirmed_removed_ids)}",
            flush=True,
        )

        for listing in active_listings_for_city:
            if listing.external_id not in confirmed_removed_ids:
                continue

            listing.is_active = False
            listing.updated_at = datetime.utcnow()

            create_listing_event(
                db=db,
                listing=listing,
                scrape_run=scrape_run,
                event_type=EVENT_REMOVED,
                old_value=listing.external_id,
                new_value=None,
            )

            removed_listings_count += 1

        finish_scrape_run(
            db=db,
            scrape_run=scrape_run,
            status="success",
            listings_found=len(listings),
            new_listings=new_listings_count,
            price_changes=price_changes_count,
            removed_listings=removed_listings_count,
            error_message=None,
        )

        db.commit()

        return scrape_run

    except Exception as exc:
        db.rollback()

        scrape_run.status = "failed"
        scrape_run.finished_at = datetime.utcnow()
        scrape_run.error_message = str(exc)

        db.add(scrape_run)
        db.commit()

        raise


def save_latest_offers_scan(
    db: Session,
    city: str,
    listings: list[dict],
) -> ScrapeRun:
    """
    Scrape z filtrem Otodom daysSinceCreated=1.

    Nowe dla naszej bazy oferty zapisujemy jako:
    - new_offer

    Ten tryb NIE oznacza removed, bo jego celem jest tylko potwierdzanie świeżych ofert.
    """

    scrape_run = create_scrape_run(db, city)

    new_offers_count = 0
    price_changes_count = 0

    try:
        for data in listings:
            _, was_new, price_changed = create_or_update_listing_from_data(
                db=db,
                scrape_run=scrape_run,
                city=city,
                data=data,
                new_listing_event_type=EVENT_NEW_OFFER,
            )

            if was_new:
                new_offers_count += 1

            if price_changed:
                price_changes_count += 1

        finish_scrape_run(
            db=db,
            scrape_run=scrape_run,
            status="success_latest_24h",
            listings_found=len(listings),
            new_listings=new_offers_count,
            price_changes=price_changes_count,
            removed_listings=0,
            error_message=None,
        )

        db.commit()

        return scrape_run

    except Exception as exc:
        db.rollback()

        scrape_run.status = "failed_latest_24h"
        scrape_run.finished_at = datetime.utcnow()
        scrape_run.error_message = str(exc)

        db.add(scrape_run)
        db.commit()

        raise


def get_recent_scrape_runs(db: Session, limit: int = 10) -> list[dict]:
    scrape_runs = (
        db.query(ScrapeRun)
        .order_by(ScrapeRun.started_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "ID runu": scrape_run.id,
            "Miasto": scrape_run.city,
            "Status": scrape_run.status,
            "Znalezione": scrape_run.listings_found,
            "Nowe / pierwszy raz widziane": scrape_run.new_listings,
            "Zmiany cen": scrape_run.price_changes,
            "Usunięte": scrape_run.removed_listings,
            "Start": scrape_run.started_at,
            "Koniec": scrape_run.finished_at,
            "Błąd": scrape_run.error_message,
        }
        for scrape_run in scrape_runs
    ]


def get_recent_listing_events(
    db: Session,
    city: str | None = None,
    limit: int = 50,
) -> list[dict]:
    query = (
        db.query(ListingEvent, Listing, ScrapeRun)
        .join(Listing, ListingEvent.listing_id == Listing.id)
        .join(ScrapeRun, ListingEvent.scrape_run_id == ScrapeRun.id)
    )

    if city:
        query = query.filter(Listing.city == city)

    rows = (
        query
        .order_by(ListingEvent.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "Event ID": event.id,
            "Typ": event.event_type,
            "Miasto": listing.city,
            "ID ogłoszenia": listing.external_id,
            "Opis": listing.title,
            "Stara wartość": event.old_value,
            "Nowa wartość": event.new_value,
            "Cena": listing.price,
            "PLN/m2": listing.price_per_m2,
            "Lokacja": listing.location,
            "Powierzchnia": listing.area,
            "Pokoje": listing.rooms,
            "URL": listing.url,
            "Data": event.created_at,
            "Scrape run": scrape_run.id,
            "Status runu": scrape_run.status,
        }
        for event, listing, scrape_run in rows
    ]


def get_recent_listing_events_by_type(
    db: Session,
    event_type: str,
    city: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    query = (
        db.query(ListingEvent, Listing, ScrapeRun)
        .join(Listing, ListingEvent.listing_id == Listing.id)
        .join(ScrapeRun, ListingEvent.scrape_run_id == ScrapeRun.id)
        .filter(ListingEvent.event_type == event_type)
    )

    if city:
        query = query.filter(Listing.city == city)

    rows = (
        query
        .order_by(ListingEvent.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "Event ID": event.id,
            "Typ": event.event_type,
            "Miasto": listing.city,
            "ID ogłoszenia": listing.external_id,
            "Opis": listing.title,
            "Cena": listing.price,
            "PLN/m2": listing.price_per_m2,
            "Lokacja": listing.location,
            "Powierzchnia": listing.area,
            "Pokoje": listing.rooms,
            "URL": listing.url,
            "Data": event.created_at,
            "Scrape run": scrape_run.id,
            "Status runu": scrape_run.status,
        }
        for event, listing, scrape_run in rows
    ]


def get_listings_from_database(
    db: Session,
    city: str | None = None,
    only_active: bool | None = None,
    limit: int = 5000,
) -> list[dict]:
    query = db.query(Listing)

    if city:
        query = query.filter(Listing.city == city)

    if only_active is True:
        query = query.filter(Listing.is_active.is_(True))

    if only_active is False:
        query = query.filter(Listing.is_active.is_(False))

    listings = (
        query
        .order_by(Listing.updated_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "DB ID": listing.id,
            "Miasto": listing.city,
            "ID ogłoszenia": listing.external_id,
            "Aktywne": listing.is_active,
            "Opis": listing.title,
            "Cena": listing.price,
            "PLN/m2": listing.price_per_m2,
            "Lokacja": listing.location,
            "Powierzchnia": listing.area,
            "Pokoje": listing.rooms,
            "URL": listing.url,
            "Pierwszy raz widziane": listing.first_seen_at,
            "Ostatni raz widziane": listing.last_seen_at,
            "Aktualizacja": listing.updated_at,
        }
        for listing in listings
    ]


def get_database_summary(db: Session, city: str | None = None) -> dict:
    query = db.query(Listing)

    if city:
        query = query.filter(Listing.city == city)

    total = query.count()
    active = query.filter(Listing.is_active.is_(True)).count()
    inactive = query.filter(Listing.is_active.is_(False)).count()

    return {
        "Wszystkie rekordy": total,
        "Aktywne": active,
        "Nieaktywne / removed": inactive,
    }


def get_event_summary(db: Session, city: str | None = None) -> list[dict]:
    query = (
        db.query(ListingEvent, Listing)
        .join(Listing, ListingEvent.listing_id == Listing.id)
    )

    if city:
        query = query.filter(Listing.city == city)

    events = query.all()

    summary = {}

    for event, listing in events:
        summary[event.event_type] = summary.get(event.event_type, 0) + 1

    return [
        {
            "Typ eventu": event_type,
            "Liczba": count,
        }
        for event_type, count in sorted(summary.items())
    ]


def get_latest_scrape_run(
    db: Session,
    city: str | None = None,
    status_prefix: str | None = None,
) -> ScrapeRun | None:
    query = db.query(ScrapeRun)

    if city:
        query = query.filter(ScrapeRun.city == city)

    if status_prefix:
        query = query.filter(ScrapeRun.status.startswith(status_prefix))

    return (
        query
        .order_by(ScrapeRun.started_at.desc())
        .first()
    )


def get_listing_events_for_scrape_run(
    db: Session,
    scrape_run_id: int,
    event_type: str | None = None,
    limit: int = 5000,
) -> list[dict]:
    query = (
        db.query(ListingEvent, Listing, ScrapeRun)
        .join(Listing, ListingEvent.listing_id == Listing.id)
        .join(ScrapeRun, ListingEvent.scrape_run_id == ScrapeRun.id)
        .filter(ScrapeRun.id == scrape_run_id)
    )

    if event_type:
        query = query.filter(ListingEvent.event_type == event_type)

    rows = (
        query
        .order_by(ListingEvent.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "Event ID": event.id,
            "Typ": event.event_type,
            "Miasto": listing.city,
            "ID ogłoszenia": listing.external_id,
            "Opis": listing.title,
            "Stara wartość": event.old_value,
            "Nowa wartość": event.new_value,
            "Cena": listing.price,
            "PLN/m2": listing.price_per_m2,
            "Lokacja": listing.location,
            "Powierzchnia": listing.area,
            "Pokoje": listing.rooms,
            "URL": listing.url,
            "Data": event.created_at,
            "Scrape run": scrape_run.id,
            "Status runu": scrape_run.status,
        }
        for event, listing, scrape_run in rows
    ]