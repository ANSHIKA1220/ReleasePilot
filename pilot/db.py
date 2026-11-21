import os
import time
from contextlib import contextmanager
from sqlalchemy import create_engine, event, insert, select
from migrations.schema_v1 import schema, users, sessions, services, artifacts, releases, events

engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///releasepilot.db"), pool_pre_ping=True)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def sqlite_setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")

@contextmanager
def transaction(write=False):
    with engine.begin() as conn:
        if write and engine.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        yield conn

def one(conn, table, ident, lock=False):
    stmt = select(table).where(table.c.id == ident)
    if lock:
        stmt = stmt.with_for_update()
    return conn.execute(stmt).mappings().first()

def audit(conn, action, actor=None, release=None, **details):
    conn.execute(insert(events).values(time=time.time(), actor=actor, release_id=release,
                                       action=action, details=details))
