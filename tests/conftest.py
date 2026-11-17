"""The tests load the made-up fixture site into their own database, never the real one.

Point PGLAB_TEST_DSN at any Postgres 14+ database the tests may wipe. Default:
a database called stacklab_test on the local lab server (created if missing).
"""
import os
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEST_DSN = os.environ.get("PGLAB_TEST_DSN", "postgresql://postgres@localhost:5544/stacklab_test")


def _ensure_database(dsn: str) -> None:
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    name = info["dbname"]
    try:
        psycopg.connect(dsn).close()
        return
    except psycopg.OperationalError:
        pass
    admin = psycopg.conninfo.make_conninfo(dsn, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))


@pytest.fixture(scope="session")
def loaded(tmp_path_factory):
    """Generate the fixture XML, load it, and hand back (xml_dir, generated rows, loaded counts)."""
    assert "test" in psycopg.conninfo.conninfo_to_dict(TEST_DSN)["dbname"], \
        "refusing to wipe a database whose name does not contain 'test'"
    _ensure_database(TEST_DSN)
    os.environ["PGLAB_DSN"] = TEST_DSN

    from lab import fixture, load

    xml_dir = tmp_path_factory.mktemp("fixture_xml")
    fixture.write(xml_dir)
    generated = fixture.build()
    counts = load.load(xml_dir, verbose=False)
    return xml_dir, generated, counts


@pytest.fixture()
def conn(loaded):
    with psycopg.connect(TEST_DSN, autocommit=True) as c:
        yield c
