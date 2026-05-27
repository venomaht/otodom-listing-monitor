import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from app.cities import CITY_URLS


LISTINGS_PER_PAGE = 72

MAX_PAGES_TO_SCAN = 300
BATCH_SIZE = 5
SCRAPE_PASSES = 2
MAX_EMPTY_PAGES_IN_ROW = 3

MIN_EXPECTED_LISTINGS = 20

HTTP_TIMEOUT = 30
HTTP_RETRIES = 3

DETAIL_CHECK_BATCH_SIZE = 20
DETAIL_HTTP_TIMEOUT = 30
DETAIL_HTTP_RETRIES = 2


class FetchMode(str, Enum):
    LIGHT_HTTP = "light_http"
    BROWSER_FALLBACK = "browser_fallback"


@dataclass
class PageResult:
    page_number: int
    requested_url: str
    final_url: str
    final_page: int
    html: str | None
    listings: list[dict]
    fetch_mode: FetchMode
    error: str | None = None
    redirected_back: bool = False


@dataclass
class ArchivedCheckResult:
    external_id: str
    url: str
    is_archived: bool
    reason: str
    status_code: int | None = None
    error: str | None = None


def clean_text(value: str | None) -> str | None:
    if not value:
        return None

    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def normalize_text(value: str | None) -> str:
    if not value:
        return ""

    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip().lower()


def parse_price(value: str | None) -> int | None:
    if not value:
        return None

    digits = re.sub(r"[^\d]", "", value)

    return int(digits) if digits else None


def parse_area_from_text(value: str | None) -> float | None:
    if not value:
        return None

    match = re.search(r"(\d+(?:[,.]\d+)?)\s*m²", value)

    return float(match.group(1).replace(",", ".")) if match else None


def parse_rooms_from_text(value: str | None) -> int | None:
    if not value:
        return None

    match = re.search(r"(\d+)\s*pok", value, re.IGNORECASE)

    return int(match.group(1)) if match else None


def calculate_price_per_m2(price: int | None, area: float | None) -> int | None:
    if not price or not area or area == 0:
        return None

    return round(price / area)


def extract_listing_id(url: str) -> str:
    """
    ID ogłoszenia jest case-sensitive.

    Nie używamy:
    - lower()
    - upper()
    - casefold()

    Przykład:
    ID54oD != ID54od
    """

    match = re.search(r"ID[a-zA-Z0-9]+", url)

    return match.group(0) if match else url.rstrip("/").split("/")[-1]


def get_default_headers() -> dict:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    }


def build_page_url(
    base_url: str,
    page_number: int,
    limit: int = LISTINGS_PER_PAGE,
) -> str:
    parsed_url = urlparse(base_url)

    query_params = parse_qs(parsed_url.query)

    query_params["limit"] = [str(limit)]

    if page_number > 1:
        query_params["page"] = [str(page_number)]
    else:
        query_params.pop("page", None)

    return urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            urlencode(query_params, doseq=True),
            parsed_url.fragment,
        )
    )


def build_latest_offers_url(base_url: str) -> str:
    """
    URL pomocniczy do pobrania ofert dodanych ostatnio według filtra Otodom.

    Używamy go jako osobnego trybu:
    - pełny scrape wykrywa newly_found
    - latest scrape wykrywa new_offer
    """

    parsed_url = urlparse(base_url)
    query_params = parse_qs(parsed_url.query)

    query_params["limit"] = [str(LISTINGS_PER_PAGE)]
    query_params["ownerTypeSingleSelect"] = ["ALL"]
    query_params["daysSinceCreated"] = ["1"]
    query_params["by"] = ["LATEST"]
    query_params["direction"] = ["DESC"]
    query_params.pop("page", None)

    return urlunparse(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            urlencode(query_params, doseq=True),
            parsed_url.fragment,
        )
    )


def extract_page_number_from_url(url: str) -> int:
    parsed_url = urlparse(url)

    query_params = parse_qs(parsed_url.query)

    page_values = query_params.get("page")

    if not page_values:
        return 1

    value = page_values[0]

    return int(value) if value.isdigit() else 1


def fetch_otodom_html_light(url: str) -> tuple[str, str]:
    headers = get_default_headers()

    last_error = None

    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            response = httpx.get(
                url,
                headers=headers,
                follow_redirects=True,
                timeout=HTTP_TIMEOUT,
            )

            response.raise_for_status()

            return response.text, str(response.url)

        except Exception as exc:
            last_error = exc

            print(
                f"HTTP retry {attempt}/{HTTP_RETRIES} failed for URL: {url}",
                flush=True,
            )

            if attempt < HTTP_RETRIES:
                time.sleep(1.5)

    raise last_error


def fetch_otodom_html_browser(url: str, city: str) -> tuple[str, str]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="pl-PL",
            viewport={"width": 1440, "height": 1200},
        )

        page = context.new_page()

        def block_unnecessary_resources(route):
            request = route.request

            if request.resource_type in ["image", "media", "font"]:
                return route.abort()

            return route.continue_()

        page.route("*/", block_unnecessary_resources)

        print(f"Opening browser fallback for city: {city}", flush=True)
        print(f"Browser URL: {url}", flush=True)

        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        try:
            page.wait_for_selector(
                'a[href*="/pl/oferta/"]',
                timeout=15000,
            )
        except Exception:
            print("Listing selector timeout in browser mode.", flush=True)

        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            print("Network idle timeout reached. Continuing anyway.", flush=True)

        html = page.content()
        final_url = page.url

        context.close()
        browser.close()

        return html, final_url


def parse_otodom_listings(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    cards = soup.select(
        'article, article[data-cy], article[data-testid], li, div[data-cy]'
    )

    listings = []
    seen_ids_on_page = set()

    print(f"Detected possible listing containers: {len(cards)}", flush=True)

    for card in cards:
        link_el = card.select_one('a[href*="/pl/oferta/"]')

        if not link_el:
            continue

        href = link_el.get("href")

        if not href:
            continue

        url = href if href.startswith("http") else f"https://www.otodom.pl{href}"

        external_id = extract_listing_id(url)

        if external_id in seen_ids_on_page:
            continue

        seen_ids_on_page.add(external_id)

        card_text = clean_text(card.get_text(" "))

        title_el = (
            card.select_one('p[data-cy="listing-item-title"]')
            or card.select_one('[data-cy="listing-item-title"]')
            or card.select_one("h1")
            or card.select_one("h2")
            or card.select_one("h3")
        )

        price_el = (
            card.select_one('span[data-sentry-element="MainPrice"]')
            or card.select_one('[data-testid="ad-price"]')
        )

        location_el = (
            card.select_one('p[data-sentry-component="Address"]')
            or card.select_one('[data-testid="location"]')
        )

        title = (
            clean_text(title_el.get_text(" "))
            if title_el
            else clean_text(link_el.get_text(" "))
        )

        raw_price = (
            clean_text(price_el.get_text(" "))
            if price_el
            else None
        )

        if not raw_price and card_text:
            price_match = re.search(
                r"(\d[\d\s]{2,})\s*zł",
                card_text,
            )

            raw_price = price_match.group(0) if price_match else None

        price = parse_price(raw_price)

        location = (
            clean_text(location_el.get_text(" "))
            if location_el
            else None
        )

        area = parse_area_from_text(card_text)
        rooms = parse_rooms_from_text(card_text)
        price_per_m2 = calculate_price_per_m2(price, area)

        listings.append(
            {
                "external_id": external_id,
                "title": title,
                "price": price,
                "price_per_m2": price_per_m2,
                "location": location,
                "area": area,
                "rooms": rooms,
                "url": url,
            }
        )

    return listings


def should_use_browser_fallback(
    listings: list[dict],
    fetch_mode: FetchMode,
) -> bool:
    if fetch_mode == FetchMode.BROWSER_FALLBACK:
        return False

    if len(listings) == 0:
        return True

    if len(listings) < MIN_EXPECTED_LISTINGS:
        print(
            f"Suspiciously low listing count detected: {len(listings)}",
            flush=True,
        )

        return True

    return False


def fetch_single_page(
    base_url: str,
    city: str,
    page_number: int,
    allow_browser_fallback: bool = True,
) -> PageResult:
    page_url = build_page_url(
        base_url,
        page_number=page_number,
    )

    print(f"Requested URL: {page_url}", flush=True)

    fetch_mode = FetchMode.LIGHT_HTTP

    try:
        html, final_url = fetch_otodom_html_light(page_url)

    except Exception as exc:
        print(f"Light HTTP failed completely for page {page_number}: {exc}", flush=True)

        if not allow_browser_fallback:
            return PageResult(
                page_number=page_number,
                requested_url=page_url,
                final_url=page_url,
                final_page=page_number,
                html=None,
                listings=[],
                fetch_mode=fetch_mode,
                error=str(exc),
                redirected_back=False,
            )

        html, final_url = fetch_otodom_html_browser(page_url, city)
        fetch_mode = FetchMode.BROWSER_FALLBACK

    final_page = extract_page_number_from_url(final_url)
    redirected_back = final_page < page_number

    print(f"Final URL: {final_url}", flush=True)
    print(
        f"Requested page: {page_number}; Final page: {final_page}",
        flush=True,
    )

    if redirected_back:
        print(
            f"Otodom redirected page {page_number} to page {final_page}.",
            flush=True,
        )

        return PageResult(
            page_number=page_number,
            requested_url=page_url,
            final_url=final_url,
            final_page=final_page,
            html=html,
            listings=[],
            fetch_mode=fetch_mode,
            error=None,
            redirected_back=True,
        )

    print(f"HTML length: {len(html)}", flush=True)

    listings = parse_otodom_listings(html)

    print(
        f"Listings parsed in {fetch_mode.value}: {len(listings)}",
        flush=True,
    )

    if allow_browser_fallback and should_use_browser_fallback(listings, fetch_mode):
        print(f"Triggering browser fallback for page {page_number}...", flush=True)

        html, final_url = fetch_otodom_html_browser(page_url, city)
        fetch_mode = FetchMode.BROWSER_FALLBACK
        final_page = extract_page_number_from_url(final_url)
        redirected_back = final_page < page_number

        print(f"Browser final URL: {final_url}", flush=True)
        print(f"Browser HTML length: {len(html)}", flush=True)

        if redirected_back:
            print(
                f"Browser fallback redirected page {page_number} to page {final_page}.",
                flush=True,
            )

            return PageResult(
                page_number=page_number,
                requested_url=page_url,
                final_url=final_url,
                final_page=final_page,
                html=html,
                listings=[],
                fetch_mode=fetch_mode,
                error=None,
                redirected_back=True,
            )

        listings = parse_otodom_listings(html)

        print(
            f"Listings parsed in browser mode: {len(listings)}",
            flush=True,
        )

    return PageResult(
        page_number=page_number,
        requested_url=page_url,
        final_url=final_url,
        final_page=final_page,
        html=html,
        listings=listings,
        fetch_mode=fetch_mode,
        error=None,
        redirected_back=redirected_back,
    )


def merge_page_listings(
    all_listings: list[dict],
    seen_ids: set[str],
    page_result: PageResult,
) -> tuple[int, int]:
    new_items_on_page = 0
    duplicate_items_on_page = 0

    for listing in page_result.listings:
        external_id = listing.get("external_id")

        if not external_id:
            continue

        if external_id in seen_ids:
            duplicate_items_on_page += 1
            continue

        seen_ids.add(external_id)
        all_listings.append(listing)

        new_items_on_page += 1

    return new_items_on_page, duplicate_items_on_page


def fetch_batch_pages(
    base_url: str,
    city: str,
    page_numbers: list[int],
) -> list[PageResult]:
    print("\n" + "-" * 80, flush=True)
    print(f"Batch mode. Pages: {page_numbers}", flush=True)
    print("-" * 80, flush=True)

    results = []

    with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
        future_to_page = {
            executor.submit(
                fetch_single_page,
                base_url,
                city,
                page_number,
                True,
            ): page_number
            for page_number in page_numbers
        }

        for future in as_completed(future_to_page):
            page_number = future_to_page[future]

            try:
                result = future.result()
                results.append(result)

            except Exception as exc:
                print(
                    f"Unexpected error while fetching page {page_number}: {exc}",
                    flush=True,
                )

                page_url = build_page_url(base_url, page_number)

                results.append(
                    PageResult(
                        page_number=page_number,
                        requested_url=page_url,
                        final_url=page_url,
                        final_page=page_number,
                        html=None,
                        listings=[],
                        fetch_mode=FetchMode.LIGHT_HTTP,
                        error=str(exc),
                        redirected_back=False,
                    )
                )

    return sorted(results, key=lambda item: item.page_number)


def collect_listings_single_pass(
    city: str,
    pass_number: int,
    base_url: str,
    scrape_label: str,
    max_pages_to_scan: int = MAX_PAGES_TO_SCAN,
) -> list[dict]:
    print("\n" + "=" * 80, flush=True)
    print(
        f"Starting scrape pass {pass_number}/{SCRAPE_PASSES} "
        f"for city: {city} | mode: {scrape_label}",
        flush=True,
    )
    print(f"Base URL: {base_url}", flush=True)
    print(f"Batch size: {BATCH_SIZE}", flush=True)
    print(f"Safety page limit: {max_pages_to_scan}", flush=True)
    print("=" * 80, flush=True)

    all_listings = []
    seen_ids = set()

    empty_pages_in_row = 0
    page_number = 1
    sequential_mode = False

    while page_number <= max_pages_to_scan:
        page_start_time = time.time()

        if sequential_mode:
            print("\n" + "-" * 80, flush=True)
            print(f"Sequential mode. Page: {page_number}", flush=True)
            print("-" * 80, flush=True)

            page_results = [
                fetch_single_page(
                    base_url=base_url,
                    city=city,
                    page_number=page_number,
                    allow_browser_fallback=True,
                )
            ]

        else:
            page_numbers = list(
                range(
                    page_number,
                    min(page_number + BATCH_SIZE, max_pages_to_scan + 1),
                )
            )

            page_results = fetch_batch_pages(
                base_url=base_url,
                city=city,
                page_numbers=page_numbers,
            )

        redirect_detected = False
        redirected_page_number = None

        for page_result in page_results:
            print("\n" + "-" * 80, flush=True)
            print(
                f"Processing parsed result for page {page_result.page_number}",
                flush=True,
            )

            if page_result.error:
                print(
                    f"Page {page_result.page_number} finished with error: {page_result.error}",
                    flush=True,
                )

            if page_result.redirected_back:
                redirect_detected = True
                redirected_page_number = page_result.page_number

                print(
                    f"Redirect detected on page {page_result.page_number}. "
                    "Switching to sequential mode.",
                    flush=True,
                )

                break

            if not page_result.listings:
                empty_pages_in_row += 1

                print(
                    f"No listings detected. Empty pages: "
                    f"{empty_pages_in_row}/{MAX_EMPTY_PAGES_IN_ROW}",
                    flush=True,
                )

                if empty_pages_in_row >= MAX_EMPTY_PAGES_IN_ROW:
                    print("Empty page safety limit reached.", flush=True)
                    return all_listings

                continue

            new_items_on_page, duplicate_items_on_page = merge_page_listings(
                all_listings=all_listings,
                seen_ids=seen_ids,
                page_result=page_result,
            )

            print(f"New unique listings: {new_items_on_page}", flush=True)
            print(f"Duplicate listings skipped: {duplicate_items_on_page}", flush=True)

            if new_items_on_page == 0:
                empty_pages_in_row += 1

                print(
                    f"No new listings added. Empty pages: "
                    f"{empty_pages_in_row}/{MAX_EMPTY_PAGES_IN_ROW}",
                    flush=True,
                )

                if empty_pages_in_row >= MAX_EMPTY_PAGES_IN_ROW:
                    print("No-new-listings safety limit reached.", flush=True)
                    return all_listings

                continue

            empty_pages_in_row = 0

        elapsed = round(time.time() - page_start_time, 2)
        print(f"Page group processed in {elapsed}s", flush=True)

        if redirect_detected:
            sequential_mode = True

            if redirected_page_number is not None:
                page_number = redirected_page_number
            else:
                page_number += 1

            if len(page_results) == 1:
                print("Redirect confirmed in sequential mode. Pagination finished.", flush=True)
                break

            continue

        if sequential_mode:
            page_number += 1
        else:
            page_number += BATCH_SIZE

    print("\n" + "=" * 80, flush=True)
    print(
        f"Scrape pass {pass_number} completed. "
        f"Total unique listings in this pass: {len(all_listings)}",
        flush=True,
    )
    print("=" * 80, flush=True)

    return all_listings


def merge_pass_results(
    combined_listings: list[dict],
    combined_seen_ids: set[str],
    pass_listings: list[dict],
    pass_number: int,
) -> tuple[int, int]:
    added = 0
    duplicates = 0

    for listing in pass_listings:
        external_id = listing.get("external_id")

        if not external_id:
            continue

        if external_id in combined_seen_ids:
            duplicates += 1
            continue

        combined_seen_ids.add(external_id)
        combined_listings.append(listing)
        added += 1

    print("\n" + "=" * 80, flush=True)
    print(
        f"Merged pass {pass_number}. "
        f"Added from this pass: {added}. "
        f"Duplicates skipped: {duplicates}. "
        f"Combined total: {len(combined_listings)}.",
        flush=True,
    )
    print("=" * 80, flush=True)

    return added, duplicates


def fetch_listings_from_base_url(
    city: str,
    base_url: str,
    scrape_label: str,
    max_pages_to_scan: int = MAX_PAGES_TO_SCAN,
) -> list[dict]:
    print("\n" + "#" * 80, flush=True)
    print(f"Starting double-cycle scrape for city: {city}", flush=True)
    print(f"Mode: {scrape_label}", flush=True)
    print(f"Base URL: {base_url}", flush=True)
    print(f"Scrape passes: {SCRAPE_PASSES}", flush=True)
    print(f"Batch size: {BATCH_SIZE}", flush=True)
    print("#" * 80, flush=True)

    combined_listings = []
    combined_seen_ids = set()

    for pass_number in range(1, SCRAPE_PASSES + 1):
        pass_listings = collect_listings_single_pass(
            city=city,
            pass_number=pass_number,
            base_url=base_url,
            scrape_label=scrape_label,
            max_pages_to_scan=max_pages_to_scan,
        )

        merge_pass_results(
            combined_listings=combined_listings,
            combined_seen_ids=combined_seen_ids,
            pass_listings=pass_listings,
            pass_number=pass_number,
        )

        if pass_number < SCRAPE_PASSES:
            print("Short pause before next scrape pass...", flush=True)
            time.sleep(3)

    print("\n" + "#" * 80, flush=True)
    print(
        f"Double-cycle scraping completed. "
        f"Mode: {scrape_label}. "
        f"Total unique listings after all passes: {len(combined_listings)}",
        flush=True,
    )
    print("#" * 80, flush=True)

    return combined_listings


def fetch_listings(city: str) -> list[dict]:
    base_url = CITY_URLS[city]

    return fetch_listings_from_base_url(
        city=city,
        base_url=base_url,
        scrape_label="full_city_scan",
        max_pages_to_scan=MAX_PAGES_TO_SCAN,
    )


def fetch_latest_offers(city: str) -> list[dict]:
    base_url = build_latest_offers_url(CITY_URLS[city])

    return fetch_listings_from_base_url(
        city=city,
        base_url=base_url,
        scrape_label="latest_24h_scan",
        max_pages_to_scan=MAX_PAGES_TO_SCAN,
    )


def fetch_listing_detail_html(url: str) -> tuple[str, str, int]:
    headers = get_default_headers()

    last_error = None

    for attempt in range(1, DETAIL_HTTP_RETRIES + 1):
        try:
            response = httpx.get(
                url,
                headers=headers,
                follow_redirects=True,
                timeout=DETAIL_HTTP_TIMEOUT,
            )

            return response.text, str(response.url), response.status_code

        except Exception as exc:
            last_error = exc

            print(
                f"Detail page retry {attempt}/{DETAIL_HTTP_RETRIES} failed for URL: {url}",
                flush=True,
            )

            if attempt < DETAIL_HTTP_RETRIES:
                time.sleep(1.0)

    raise last_error


def is_archived_listing_html(html: str) -> tuple[bool, str]:
    normalized_html = normalize_text(html)

    archived_signals = [
        "to ogłoszenie jest już niedostępne",
        "ogłoszenie jest już niedostępne",
        "to ogloszenie jest juz niedostepne",
        "ogloszenie jest juz niedostepne",
        "oferta archiwalna",
        "ogłoszenie archiwalne",
        "ogloszenie archiwalne",
        "nieruchomość ma już nowego właściciela",
        "nieruchomosc ma juz nowego wlasciciela",
        "expiredadalert",
        "expired ad alert",
        "archiwalne",
    ]

    for signal in archived_signals:
        if signal in normalized_html:
            return True, f"archived_signal_found: {signal}"

    return False, "no_archived_signal_found"


def check_single_removed_candidate(candidate: dict) -> ArchivedCheckResult:
    external_id = candidate.get("external_id")
    url = candidate.get("url")

    if not external_id:
        return ArchivedCheckResult(
            external_id="UNKNOWN",
            url=url or "",
            is_archived=False,
            reason="missing_external_id",
            status_code=None,
            error="Missing external_id",
        )

    if not url:
        return ArchivedCheckResult(
            external_id=external_id,
            url="",
            is_archived=False,
            reason="missing_url",
            status_code=None,
            error="Missing URL",
        )

    try:
        html, final_url, status_code = fetch_listing_detail_html(url)

        print(
            f"Checked detail page for {external_id}. "
            f"Status: {status_code}. Final URL: {final_url}",
            flush=True,
        )

        if status_code in [404, 410]:
            return ArchivedCheckResult(
                external_id=external_id,
                url=url,
                is_archived=True,
                reason=f"http_status_{status_code}",
                status_code=status_code,
                error=None,
            )

        if status_code >= 500:
            return ArchivedCheckResult(
                external_id=external_id,
                url=url,
                is_archived=False,
                reason=f"server_error_status_{status_code}",
                status_code=status_code,
                error=None,
            )

        is_archived, reason = is_archived_listing_html(html)

        return ArchivedCheckResult(
            external_id=external_id,
            url=url,
            is_archived=is_archived,
            reason=reason,
            status_code=status_code,
            error=None,
        )

    except Exception as exc:
        print(
            f"Could not validate removed candidate {external_id}: {exc}",
            flush=True,
        )

        return ArchivedCheckResult(
            external_id=external_id,
            url=url,
            is_archived=False,
            reason="validation_error_keep_active",
            status_code=None,
            error=str(exc),
        )


def validate_removed_candidates(
    candidates: list[dict],
) -> set[str]:
    if not candidates:
        print("No removed candidates to validate.", flush=True)
        return set()

    print("\n" + "=" * 80, flush=True)
    print(
        f"Validating removed candidates by checking detail pages. "
        f"Candidates: {len(candidates)}. "
        f"Batch size: {DETAIL_CHECK_BATCH_SIZE}",
        flush=True,
    )
    print("=" * 80, flush=True)

    confirmed_removed_ids = set()
    checked_count = 0

    with ThreadPoolExecutor(max_workers=DETAIL_CHECK_BATCH_SIZE) as executor:
        future_to_candidate = {
            executor.submit(check_single_removed_candidate, candidate): candidate
            for candidate in candidates
        }

        for future in as_completed(future_to_candidate):
            checked_count += 1

            try:
                result = future.result()

            except Exception as exc:
                candidate = future_to_candidate[future]
                print(
                    f"Unexpected validation error for candidate "
                    f"{candidate.get('external_id')}: {exc}",
                    flush=True,
                )
                continue

            print(
                f"[{checked_count}/{len(candidates)}] "
                f"{result.external_id} | archived={result.is_archived} | "
                f"reason={result.reason} | status={result.status_code}",
                flush=True,
            )

            if result.is_archived:
                confirmed_removed_ids.add(result.external_id)

    print("\n" + "=" * 80, flush=True)
    print(
        f"Removed validation finished. "
        f"Confirmed removed: {len(confirmed_removed_ids)} / {len(candidates)}",
        flush=True,
    )
    print("=" * 80, flush=True)

    return confirmed_removed_ids