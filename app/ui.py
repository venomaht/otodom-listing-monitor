import pandas as pd
import streamlit as st

from app.cities import CITY_URLS, DEFAULT_CITY
from app.database import SessionLocal, init_db
from app.repository import (
    EVENT_NEWLY_FOUND,
    EVENT_NEW_OFFER,
    get_database_summary,
    get_event_summary,
    get_latest_scrape_run,
    get_listing_events_for_scrape_run,
    get_listings_from_database,
    get_recent_listing_events,
    get_recent_listing_events_by_type,
    get_recent_scrape_runs,
    save_latest_offers_scan,
    save_scraped_listings,
)
from app.scraper import fetch_latest_offers, fetch_listings


TECHNICAL_COLUMNS = [
    "external_id",
    "title",
    "price",
    "price_per_m2",
    "location",
    "area",
    "rooms",
    "url",
]

DISPLAY_COLUMNS_MAP = {
    "external_id": "ID",
    "title": "Opis",
    "price": "Cena",
    "price_per_m2": "PLN/m2",
    "location": "Lokacja",
    "area": "Powierzchnia",
    "rooms": "Pokoje",
    "url": "URL",
}

FINAL_DISPLAY_COLUMNS = [
    "ID",
    "Opis",
    "Cena",
    "PLN/m2",
    "Lokacja",
    "Powierzchnia",
    "Pokoje",
    "URL",
]


def prepare_display_dataframe(listings: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(listings)

    for column in TECHNICAL_COLUMNS:
        if column not in df.columns:
            df[column] = None

    df = df[TECHNICAL_COLUMNS]
    df = df.rename(columns=DISPLAY_COLUMNS_MAP)
    df = df[FINAL_DISPLAY_COLUMNS]

    return df


def filter_dataframe_by_text(
    df: pd.DataFrame,
    search_text: str,
) -> pd.DataFrame:
    if not search_text:
        return df

    search_text_lower = search_text.lower()

    return df[
        df.astype(str)
        .apply(
            lambda row: row.str.lower().str.contains(search_text_lower, na=False).any(),
            axis=1,
        )
    ]


def render_full_scrape_tab(db, city: str):
    st.subheader("🚀 Pełny scrape miasta")

    st.write(
        "Ten tryb pobiera pełną listę ogłoszeń dla miasta. "
        "Jeżeli system zobaczy ID pierwszy raz, zapisze event `newly_found`. "
        "To oznacza: pierwszy raz znalezione przez nasz system, a niekoniecznie świeżo dodane na Otodom."
    )

    st.warning(
        "Ten tryb może oznaczyć oferty jako `removed`, ale dopiero po dodatkowym sprawdzeniu "
        "strony szczegółowej ogłoszenia i potwierdzeniu, że oferta jest archiwalna/niedostępna."
    )

    fetch_button = st.button(
        "Pobierz pełne dane miasta i zapisz do bazy",
        type="primary",
        key="full_scrape_button",
    )

    if fetch_button:
        with st.spinner(f"Pełne pobieranie ogłoszeń dla miasta: {city}..."):
            listings = fetch_listings(city)

            if not listings:
                st.warning("Nie znaleziono ogłoszeń.")
                return

            scrape_run = save_scraped_listings(
                db=db,
                city=city,
                listings=listings,
            )

            df = prepare_display_dataframe(listings)

            st.success(
                f"Zapisano scrape run #{scrape_run.id}. "
                f"Pobrano: {scrape_run.listings_found}, "
                f"newly_found: {scrape_run.new_listings}, "
                f"zmiany cen: {scrape_run.price_changes}, "
                f"removed: {scrape_run.removed_listings}."
            )

            st.subheader("Wynik pełnego pobrania")

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )


def render_latest_24h_scrape_tab(db, city: str):
    st.subheader("🆕 Sprawdź faktycznie nowe oferty z ostatnich 24h")

    st.write(
        "Ten tryb używa filtra Otodom `daysSinceCreated=1`, sortowania po najnowszych "
        "i zapisuje nowe dla bazy oferty jako event `new_offer`."
    )

    st.info(
        "Ten tryb nie oznacza ofert jako removed. Służy tylko do wykrywania świeżych ofert."
    )

    fetch_button = st.button(
        "Pobierz oferty z ostatnich 24h i zapisz do bazy",
        type="primary",
        key="latest_24h_scrape_button",
    )

    if fetch_button:
        with st.spinner(f"Pobieranie ofert z ostatnich 24h dla miasta: {city}..."):
            listings = fetch_latest_offers(city)

            if not listings:
                st.warning("Nie znaleziono ofert z ostatnich 24h.")
                return

            scrape_run = save_latest_offers_scan(
                db=db,
                city=city,
                listings=listings,
            )

            df = prepare_display_dataframe(listings)

            st.success(
                f"Zapisano latest 24h scrape run #{scrape_run.id}. "
                f"Pobrano: {scrape_run.listings_found}, "
                f"new_offer: {scrape_run.new_listings}, "
                f"zmiany cen: {scrape_run.price_changes}."
            )

            st.subheader("Wynik pobrania ofert z ostatnich 24h")

            st.dataframe(
                df,
                use_container_width=True,
                hide_index=True,
            )


def render_new_offers_tab(db, city: str):
    st.subheader("🆕 New offer — faktycznie świeże oferty")

    st.write(
        "Ten widok pokazuje eventy `new_offer`, czyli oferty znalezione przez tryb "
        "`daysSinceCreated=1`. To jest najlepszy widok do sprawdzania realnie nowych ofert."
    )

    scope = st.radio(
        "Zakres",
        options=[
            "Tylko wybrane miasto",
            "Wszystkie miasta",
        ],
        horizontal=True,
        key="new_offers_scope",
    )

    city_filter = city if scope == "Tylko wybrane miasto" else None

    latest_run = get_latest_scrape_run(
        db=db,
        city=city_filter,
        status_prefix="success_latest_24h",
    )

    if latest_run:
        col1, col2, col3, col4 = st.columns(4)

        col1.metric("Ostatni latest run", f"#{latest_run.id}")
        col2.metric("Znalezione", latest_run.listings_found)
        col3.metric("New offer", latest_run.new_listings)
        col4.metric("Zmiany cen", latest_run.price_changes)

        st.caption(
            f"Miasto: {latest_run.city} | "
            f"Start: {latest_run.started_at} | "
            f"Koniec: {latest_run.finished_at}"
        )
    else:
        st.info("Nie ma jeszcze żadnego scrape runu latest 24h.")

    limit = st.number_input(
        "Limit eventów new_offer",
        min_value=50,
        max_value=20000,
        value=5000,
        step=50,
        key="new_offers_limit",
    )

    events = get_recent_listing_events_by_type(
        db=db,
        event_type=EVENT_NEW_OFFER,
        city=city_filter,
        limit=int(limit),
    )

    if not events:
        st.info("Brak eventów `new_offer` dla wybranego zakresu.")
        return

    df = pd.DataFrame(events)

    search_text = st.text_input(
        "Szukaj w new_offer",
        value="",
        key="new_offers_search",
    )

    df = filter_dataframe_by_text(df, search_text)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )


def render_newly_found_tab(db, city: str):
    st.subheader("🔎 Newly found — pierwszy raz znalezione przez scraper")

    st.write(
        "Ten widok pokazuje eventy `newly_found`, czyli oferty, których wcześniej nie było "
        "w naszej bazie podczas pełnego scrape miasta. To nie musi oznaczać, że są świeżo dodane na Otodom."
    )

    scope = st.radio(
        "Zakres",
        options=[
            "Tylko wybrane miasto",
            "Wszystkie miasta",
        ],
        horizontal=True,
        key="newly_found_scope",
    )

    city_filter = city if scope == "Tylko wybrane miasto" else None

    latest_run = get_latest_scrape_run(
        db=db,
        city=city_filter,
        status_prefix="success",
    )

    if latest_run:
        col1, col2, col3, col4, col5 = st.columns(5)

        col1.metric("Ostatni run", f"#{latest_run.id}")
        col2.metric("Status", latest_run.status)
        col3.metric("Znalezione", latest_run.listings_found)
        col4.metric("Newly found", latest_run.new_listings)
        col5.metric("Removed", latest_run.removed_listings)

    limit = st.number_input(
        "Limit eventów newly_found",
        min_value=50,
        max_value=20000,
        value=5000,
        step=50,
        key="newly_found_limit",
    )

    events = get_recent_listing_events_by_type(
        db=db,
        event_type=EVENT_NEWLY_FOUND,
        city=city_filter,
        limit=int(limit),
    )

    if not events:
        st.info("Brak eventów `newly_found` dla wybranego zakresu.")
        return

    df = pd.DataFrame(events)

    search_text = st.text_input(
        "Szukaj w newly_found",
        value="",
        key="newly_found_search",
    )

    df = filter_dataframe_by_text(df, search_text)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )


def render_latest_changes_tab(db, city: str):
    st.subheader("🔍 Zmiany z ostatniego pobrania")

    st.write(
        "Ten widok pokazuje wszystkie eventy z ostatniego scrape runu."
    )

    scope = st.radio(
        "Zakres zmian",
        options=[
            "Tylko wybrane miasto",
            "Wszystkie miasta",
        ],
        horizontal=True,
        key="latest_changes_scope",
    )

    city_filter = city if scope == "Tylko wybrane miasto" else None

    latest_run = get_latest_scrape_run(
        db=db,
        city=city_filter,
    )

    if latest_run is None:
        st.info("Brak scrape runów w bazie.")
        return

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    col1.metric("Scrape run", f"#{latest_run.id}")
    col2.metric("Status", latest_run.status)
    col3.metric("Znalezione", latest_run.listings_found)
    col4.metric("Nowe / pierwszy raz", latest_run.new_listings)
    col5.metric("Zmiany cen", latest_run.price_changes)
    col6.metric("Removed", latest_run.removed_listings)

    latest_events = get_listing_events_for_scrape_run(
        db=db,
        scrape_run_id=latest_run.id,
        event_type=None,
        limit=10000,
    )

    if not latest_events:
        st.info("Ostatni scrape run nie utworzył żadnych eventów.")
        return

    df_events = pd.DataFrame(latest_events)

    available_event_types = sorted(df_events["Typ"].dropna().unique())

    selected_event_types = st.multiselect(
        "Typ eventu",
        options=available_event_types,
        default=available_event_types,
        key="latest_changes_type_filter",
    )

    if selected_event_types:
        df_events = df_events[df_events["Typ"].isin(selected_event_types)]

    search_text = st.text_input(
        "Szukaj w zmianach z ostatniego pobrania",
        value="",
        key="latest_changes_search",
    )

    df_events = filter_dataframe_by_text(df_events, search_text)

    st.dataframe(
        df_events,
        use_container_width=True,
        hide_index=True,
    )


def render_database_tab(db, city: str):
    st.subheader("🗄️ Pełna baza ogłoszeń")

    st.write(
        "Ten widok pokazuje aktualny stan tabeli `listings`. "
        "Status życiowy ogłoszenia to aktywne albo removed/nieaktywne. "
        "`newly_found` i `new_offer` są eventami historycznymi, nie statusem ogłoszenia."
    )

    scope = st.radio(
        "Zakres danych",
        options=[
            "Tylko wybrane miasto",
            "Wszystkie miasta",
        ],
        horizontal=True,
        key="database_scope",
    )

    city_filter = city if scope == "Tylko wybrane miasto" else None

    status_filter = st.selectbox(
        "Status ogłoszenia",
        options=[
            "Wszystkie",
            "Tylko aktywne",
            "Tylko removed / nieaktywne",
        ],
        key="database_status_filter",
    )

    if status_filter == "Tylko aktywne":
        only_active = True
    elif status_filter == "Tylko removed / nieaktywne":
        only_active = False
    else:
        only_active = None

    limit = st.number_input(
        "Limit rekordów do wyświetlenia",
        min_value=100,
        max_value=20000,
        value=5000,
        step=100,
        key="database_limit",
    )

    summary = get_database_summary(
        db=db,
        city=city_filter,
    )

    col1, col2, col3 = st.columns(3)

    col1.metric("Wszystkie rekordy", summary["Wszystkie rekordy"])
    col2.metric("Aktywne", summary["Aktywne"])
    col3.metric("Removed / nieaktywne", summary["Nieaktywne / removed"])

    listings = get_listings_from_database(
        db=db,
        city=city_filter,
        only_active=only_active,
        limit=int(limit),
    )

    if not listings:
        st.info("Brak rekordów w bazie dla wybranego zakresu.")
        return

    df = pd.DataFrame(listings)

    search_text = st.text_input(
        "Szukaj po ID, opisie, lokalizacji lub URL",
        value="",
        key="database_search",
    )

    df = filter_dataframe_by_text(df, search_text)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )


def render_events_tab(db, city: str):
    st.subheader("🧾 Wszystkie eventy")

    st.write(
        "Ten widok pokazuje pełną historię eventów: "
        "`newly_found`, `new_offer`, `price_change`, `removed`."
    )

    scope = st.radio(
        "Zakres eventów",
        options=[
            "Tylko wybrane miasto",
            "Wszystkie miasta",
        ],
        horizontal=True,
        key="events_scope",
    )

    city_filter = city if scope == "Tylko wybrane miasto" else None

    event_summary = get_event_summary(
        db=db,
        city=city_filter,
    )

    if event_summary:
        df_event_summary = pd.DataFrame(event_summary)

        st.write("Podsumowanie eventów:")

        st.dataframe(
            df_event_summary,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("Brak eventów dla wybranego zakresu.")

    event_limit = st.number_input(
        "Limit eventów do wyświetlenia",
        min_value=50,
        max_value=20000,
        value=1000,
        step=50,
        key="events_limit",
    )

    events = get_recent_listing_events(
        db=db,
        city=city_filter,
        limit=int(event_limit),
    )

    if not events:
        st.info("Brak zapisanych eventów.")
        return

    df_events = pd.DataFrame(events)

    available_event_types = sorted(df_events["Typ"].dropna().unique())

    selected_event_types = st.multiselect(
        "Typ eventu",
        options=available_event_types,
        default=available_event_types,
        key="events_type_filter",
    )

    if selected_event_types:
        df_events = df_events[df_events["Typ"].isin(selected_event_types)]

    search_text = st.text_input(
        "Szukaj w eventach",
        value="",
        key="events_search",
    )

    df_events = filter_dataframe_by_text(df_events, search_text)

    st.dataframe(
        df_events,
        use_container_width=True,
        hide_index=True,
    )


def render_scrape_runs_tab(db):
    st.subheader("📊 Historia uruchomień")

    limit = st.number_input(
        "Limit scrape runów",
        min_value=10,
        max_value=1000,
        value=100,
        step=10,
        key="scrape_runs_limit",
    )

    scrape_runs = get_recent_scrape_runs(
        db=db,
        limit=int(limit),
    )

    if not scrape_runs:
        st.info("Brak zapisanych uruchomień.")
        return

    df_runs = pd.DataFrame(scrape_runs)

    st.dataframe(
        df_runs,
        use_container_width=True,
        hide_index=True,
    )


init_db()

st.set_page_config(
    page_title="Otodom Listing Monitor",
    page_icon="🏠",
    layout="wide",
)

st.title("🏠 Otodom Listing Monitor")

st.write(
    "Aplikacja pobiera ogłoszenia mieszkań z Otodom dla wybranej lokalizacji, "
    "zapisuje dane do PostgreSQL i pokazuje historię zmian."
)

use_default_city = st.checkbox(
    f"Use default city: {DEFAULT_CITY}",
    value=True,
)

city_options = sorted(CITY_URLS.keys())

selected_city = st.selectbox(
    "Choose city",
    options=city_options,
    index=city_options.index(DEFAULT_CITY),
    disabled=use_default_city,
)

city = DEFAULT_CITY if use_default_city else selected_city

st.info(f"Selected city: {city}")

db = SessionLocal()

try:
    (
        tab_full_scrape,
        tab_latest_24h_scrape,
        tab_new_offers,
        tab_newly_found,
        tab_latest_changes,
        tab_database,
        tab_events,
        tab_runs,
    ) = st.tabs(
        [
            "🚀 Pełny scrape",
            "🆕 Scrape 24h",
            "✅ New offer",
            "🔎 Newly found",
            "🔍 Ostatnie zmiany",
            "🗄️ Baza danych",
            "🧾 Wszystkie eventy",
            "📊 Scrape runy",
        ]
    )

    with tab_full_scrape:
        render_full_scrape_tab(db, city)

    with tab_latest_24h_scrape:
        render_latest_24h_scrape_tab(db, city)

    with tab_new_offers:
        render_new_offers_tab(db, city)

    with tab_newly_found:
        render_newly_found_tab(db, city)

    with tab_latest_changes:
        render_latest_changes_tab(db, city)

    with tab_database:
        render_database_tab(db, city)

    with tab_events:
        render_events_tab(db, city)

    with tab_runs:
        render_scrape_runs_tab(db)

except Exception as exc:
    st.error("Wystąpił błąd podczas działania aplikacji.")
    st.exception(exc)

finally:
    db.close()