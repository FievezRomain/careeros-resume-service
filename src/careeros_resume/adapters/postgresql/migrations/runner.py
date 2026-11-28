import argparse

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    inspect,
    select,
    text,
)

HEAD = "0001_resume_versions"
schema = MetaData()
ledger = Table("resume_schema", MetaData(), Column("revision", String(80), primary_key=True))
documents = Table(
    "resume_documents",
    schema,
    Column("tenant_id", String(36), primary_key=True),
    Column("account_id", String(36), primary_key=True),
    Column("resume_id", String(36), primary_key=True),
)
versions = Table(
    "resume_versions",
    schema,
    Column("tenant_id", String(36), primary_key=True),
    Column("account_id", String(36), primary_key=True),
    Column("resume_id", String(36), primary_key=True),
    Column("number", Integer, primary_key=True),
    Column("request_key", String(36), nullable=False),
    Column("payload", Text, nullable=False),
    UniqueConstraint("tenant_id", "account_id", "resume_id", "request_key"),
)


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", frozen=True, hide_input_in_errors=True
    )
    resume_database_url: SecretStr

    @field_validator("resume_database_url")
    @classmethod
    def postgres_url(cls, url: SecretStr) -> SecretStr:
        if not url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("RESUME_DATABASE_URL must use PostgreSQL psycopg")
        return url


class SqlMigrations:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def apply_pending(self) -> None:
        with self._engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(851270001)"))
            ledger.create(connection, checkfirst=True)
            if not tuple(connection.scalars(select(ledger.c.revision))):
                schema.create_all(connection, checkfirst=False)
                connection.execute(ledger.insert().values(revision=HEAD))

    def verify(self) -> bool:
        with self._engine.connect() as connection:
            inspector = inspect(connection)
            return (
                inspector.has_table(ledger.name)
                and tuple(connection.scalars(select(ledger.c.revision))) == (HEAD,)
                and all(
                    inspector.has_table(table.name)
                    and {column["name"] for column in inspector.get_columns(table.name)}
                    == set(table.c.keys())
                    for table in schema.tables.values()
                )
            )

    def downgrade(self) -> None:
        with self._engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SELECT pg_advisory_xact_lock(851270001)"))
            if not inspect(connection).has_table(ledger.name):
                return
            if tuple(connection.scalars(select(ledger.c.revision))) != (HEAD,):
                raise RuntimeError("unexpected resume schema revision")
            schema.drop_all(connection, checkfirst=False)
            ledger.drop(connection)


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the owned resume database")
    parser.add_argument("operation", choices=("upgrade", "downgrade"))
    args = parser.parse_args()
    engine = create_engine(DatabaseSettings().resume_database_url.get_secret_value())
    try:
        migrations = SqlMigrations(engine)
        if args.operation == "upgrade":
            migrations.apply_pending()
            return int(not migrations.verify())
        migrations.downgrade()
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
