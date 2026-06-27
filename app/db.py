from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


def create_db_engine(settings: Settings):
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)


def create_session_factory(settings: Settings) -> sessionmaker[Session]:
    return sessionmaker(autocommit=False, autoflush=False, bind=create_db_engine(settings))


def init_db(settings: Settings) -> sessionmaker[Session]:
    engine = create_db_engine(settings)
    Base.metadata.create_all(bind=engine)
    ensure_compatible_schema(engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_compatible_schema(engine) -> None:
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "email" in user_columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(255)"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"))


def session_dependency(session_factory: sessionmaker[Session]):
    def get_db() -> Generator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    return get_db
