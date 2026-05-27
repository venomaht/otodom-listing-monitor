import os


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://postgres:postgres@db:5432/otodom",
)

# Ważne:
# Ta wartość musi odpowiadać kluczowi z CITY_URLS w app/cities.py.
# Dlatego domyślnie używamy "Łódź", a nie "lodz".
SCRAPE_CITY = os.getenv("SCRAPE_CITY", "Łódź")

# Co ile minut worker ma automatycznie uruchamiać monitoring.
SCRAPE_INTERVAL_MINUTES = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "360"))

# Czy worker ma wykonać scrape od razu po starcie kontenera.
RUN_ON_STARTUP = os.getenv("RUN_ON_STARTUP", "true").lower() == "true"

# Czy w ramach jednego cyklu uruchamiać pełny scrape miasta.
ENABLE_FULL_SCRAPE = os.getenv("ENABLE_FULL_SCRAPE", "true").lower() == "true"

# Czy w ramach jednego cyklu uruchamiać scrape ofert z ostatnich 24h.
ENABLE_LATEST_24H_SCRAPE = os.getenv("ENABLE_LATEST_24H_SCRAPE", "true").lower() == "true"

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"