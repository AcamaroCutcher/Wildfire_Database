"""Acceptance tests: the schema, the linking, and the 20 questions from the project proposal.

  WF_DSN="dbname=wildfire" python -m pytest tests -q
Each proposal query must run and return at least one row on the loaded database.
Link-quality checks make sure the hotspot -> fire join behaves.
"""
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'ingest'))
from common import connect  # noqa: E402

QUERIES = sorted((pathlib.Path(__file__).resolve().parents[1] / 'queries' / 'proposal').glob('*.sql'))


@pytest.fixture(scope='module')
def conn():
    with connect() as c:
        c.execute("SET statement_timeout = '10min'")
        yield c


@pytest.mark.parametrize('path', QUERIES, ids=[p.stem for p in QUERIES])
def test_proposal_query_answers(conn, path):
    rows = conn.execute(path.read_text()).fetchall()
    assert rows, f'{path.name} returned no rows'


def test_every_cause_code_has_a_description(conn):
    bad = conn.execute("SELECT count(*) FROM fire_event e LEFT JOIN lk_cause c ON c.code = e.cause_code "
                       "WHERE e.cause_code IS NOT NULL AND c.description IS NULL").fetchone()[0]
    assert bad == 0


def test_confidence_is_never_mixed(conn):
    # VIIRS never gets a fake percentage; MODIS always has one
    viirs_pct = conn.execute("SELECT count(*) FROM hotspot h JOIN sensor s USING (sensor_id) "
                             "WHERE s.instrument = 'VIIRS' AND h.confidence_pct IS NOT NULL").fetchone()[0]
    modis_missing = conn.execute("SELECT count(*) FROM hotspot h JOIN sensor s USING (sensor_id) "
                                 "WHERE s.instrument = 'MODIS' AND h.confidence_pct IS NULL").fetchone()[0]
    assert viirs_pct == 0 and modis_missing == 0


def test_no_hotspot_linked_twice(conn):
    dup = conn.execute("SELECT count(*) FROM (SELECT hotspot_id, acq_time FROM hotspot_event_link "
                       "GROUP BY 1, 2 HAVING count(*) > 1) d").fetchone()[0]
    assert dup == 0


def test_links_respect_time_window(conn):
    bad = conn.execute("""SELECT count(*) FROM hotspot_event_link l JOIN fire_event e USING (event_id)
                          WHERE NOT (l.acq_time <@ fire_window(e))""").fetchone()[0]
    assert bad == 0


def test_most_strong_links_are_inside_a_perimeter(conn):
    share = conn.execute("""SELECT avg((method IN ('inside_perimeter_same_time', 'inside_final_perimeter'))::int)
                            FROM hotspot_event_link WHERE score >= 0.6""").fetchone()[0]
    assert share is not None and share > 0.5


def test_perimeters_are_valid_and_dated(conn):
    bad = conn.execute("SELECT count(*) FROM perimeter_obs WHERE NOT ST_IsValid(geom) OR observed_at IS NULL").fetchone()[0]
    assert bad == 0


def test_event_bundle_is_complete(conn):
    eid = conn.execute("""SELECT event_id FROM fire_event e WHERE EXISTS (SELECT 1 FROM hotspot_event_link l WHERE l.event_id = e.event_id)
                          ORDER BY coalesce(reported_acres, final_acres) DESC NULLS LAST LIMIT 1""").fetchone()[0]
    b = conn.execute('SELECT event_bundle(%s)', (eid,)).fetchone()[0]
    assert b['event']['incident_name'] and b['perimeters'] and b['hotspots']
