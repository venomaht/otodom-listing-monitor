import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def wait_for_database(
    retries: int = 30,
    delay_seconds: int = 2,
) -> None:
    """
    Czeka aż PostgreSQL będzie gotowy do przyjmowania połączeń.

    docker compose depends_on gwarantuje tylko start kontenera,
    ale nie gwarantuje, że baza jest już gotowa.
    """

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))

            print("Database connection successful.", flush=True)
            return

        except Exception as exc:
            last_error = exc

            print(
                f"Database not ready yet. "
                f"Attempt {attempt}/{retries}. "
                f"Retrying in {delay_seconds}s...",
                flush=True,
            )

            time.sleep(delay_seconds)

    raise RuntimeError(
        f"Database was not ready after {retries} attempts. Last error: {last_error}"
    )


def init_db() -> None:
    import app.models  # noqa: F401

    wait_for_database()
    Base.metadata.create_all(bind=engine)