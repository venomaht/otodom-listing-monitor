import time
from datetime import datetime

from app.config import (
    ENABLE_FULL_SCRAPE,
    ENABLE_LATEST_24H_SCRAPE,
    RUN_ON_STARTUP,
    SCRAPE_CITY,
    SCRAPE_INTERVAL_MINUTES,
)
from app.database import SessionLocal, init_db
from app.repository import save_latest_offers_scan, save_scraped_listings
from app.scraper import fetch_latest_offers, fetch_listings


def run_monitoring_cycle(city: str = SCRAPE_CITY) -> None:
    """
    Wykonuje jeden pełny cykl monitoringu:
    1. opcjonalnie pełny scrape miasta,
    2. opcjonalnie scrape ofert z ostatnich 24h,
    3. zapis wyników i eventów do bazy.

    Pełny scrape odpowiada za:
    - newly_found,
    - price_change,
    - removed.

    Scrape 24h odpowiada za:
    - new_offer,
    - price_change.
    """

    cycle_started_at = datetime.utcnow()

    print("\n" + "=" * 100, flush=True)
    print(f"Monitoring cycle started at UTC: {cycle_started_at}", flush=True)
    print(f"City: {city}", flush=True)
    print(f"ENABLE_FULL_SCRAPE: {ENABLE_FULL_SCRAPE}", flush=True)
    print(f"ENABLE_LATEST_24H_SCRAPE: {ENABLE_LATEST_24H_SCRAPE}", flush=True)
    print("=" * 100, flush=True)

    db = SessionLocal()

    try:
        if ENABLE_FULL_SCRAPE:
            print("\nStarting full city scrape...", flush=True)

            full_listings = fetch_listings(city)

            print(
                f"Full city scrape finished. Listings collected: {len(full_listings)}",
                flush=True,
            )

            full_scrape_run = save_scraped_listings(
                db=db,
                city=city,
                listings=full_listings,
            )

            print(
                f"Full scrape saved. "
                f"Run ID: {full_scrape_run.id}. "
                f"Found: {full_scrape_run.listings_found}. "
                f"Newly found: {full_scrape_run.new_listings}. "
                f"Price changes: {full_scrape_run.price_changes}. "
                f"Removed: {full_scrape_run.removed_listings}.",
                flush=True,
            )

        else:
            print("Full city scrape disabled. Skipping.", flush=True)

        if ENABLE_LATEST_24H_SCRAPE:
            print("\nStarting latest 24h scrape...", flush=True)

            latest_listings = fetch_latest_offers(city)

            print(
                f"Latest 24h scrape finished. Listings collected: {len(latest_listings)}",
                flush=True,
            )

            latest_scrape_run = save_latest_offers_scan(
                db=db,
                city=city,
                listings=latest_listings,
            )

            print(
                f"Latest 24h scrape saved. "
                f"Run ID: {latest_scrape_run.id}. "
                f"Found: {latest_scrape_run.listings_found}. "
                f"New offers: {latest_scrape_run.new_listings}. "
                f"Price changes: {latest_scrape_run.price_changes}.",
                flush=True,
            )

        else:
            print("Latest 24h scrape disabled. Skipping.", flush=True)

        cycle_finished_at = datetime.utcnow()

        print("\n" + "=" * 100, flush=True)
        print(f"Monitoring cycle finished at UTC: {cycle_finished_at}", flush=True)
        print("=" * 100, flush=True)

    except Exception as exc:
        print("\n" + "!" * 100, flush=True)
        print("Monitoring cycle failed.", flush=True)
        print(f"Error: {exc}", flush=True)
        print("!" * 100, flush=True)

        raise

    finally:
        db.close()


def start_scheduler() -> None:
    """
    Prosty scheduler oparty o pętlę while.

    Celowo nie komplikujemy tego APSchedulerem, bo dla tego projektu wystarczy:
    - docker uruchamia worker,
    - worker wykonuje cykl,
    - worker czeka określoną liczbę minut,
    - worker powtarza cykl.
    """

    print("Starting Otodom monitoring worker...", flush=True)
    print(f"Configured city: {SCRAPE_CITY}", flush=True)
    print(f"Scrape interval: {SCRAPE_INTERVAL_MINUTES} minutes", flush=True)
    print(f"Run on startup: {RUN_ON_STARTUP}", flush=True)

    init_db()

    if RUN_ON_STARTUP:
        try:
            run_monitoring_cycle(SCRAPE_CITY)
        except Exception as exc:
            print(
                f"Startup monitoring cycle failed, but worker will continue. Error: {exc}",
                flush=True,
            )
    else:
        print("RUN_ON_STARTUP is false. First cycle will run after interval.", flush=True)

    sleep_seconds = SCRAPE_INTERVAL_MINUTES * 60

    while True:
        print(
            f"\nWorker sleeping for {SCRAPE_INTERVAL_MINUTES} minutes...",
            flush=True,
        )

        time.sleep(sleep_seconds)

        try:
            run_monitoring_cycle(SCRAPE_CITY)
        except Exception as exc:
            print(
                f"Scheduled monitoring cycle failed, but worker will continue. Error: {exc}",
                flush=True,
            )