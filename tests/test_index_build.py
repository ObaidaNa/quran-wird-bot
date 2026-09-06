"""Mushaf index tests; skipped when the index has not been built yet."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from quran_wird.db.models import Ayah, PageIndex, Surah
from quran_wird.db.session import create_engine_sync, sync_session_scope

DB = Path(__file__).resolve().parents[1] / "data" / "bot.db"

pytestmark = pytest.mark.skipif(not DB.exists(), reason="run: uv run scripts/build_index.py")


@pytest.fixture(scope="module")
def session():
    engine = create_engine_sync(DB)
    with sync_session_scope(engine) as s:
        yield s
    engine.dispose()


def test_counts(session):
    assert session.scalar(select(func.count()).select_from(Surah)) == 114
    assert session.scalar(select(func.count()).select_from(Ayah)) == 6236
    assert session.scalar(select(func.count()).select_from(PageIndex)) == 604


def test_every_page_present(session):
    pages = set(session.scalars(select(PageIndex.page_no)))
    assert pages == set(range(1, 605))


def test_first_page_is_al_fatiha(session):
    p = session.get(PageIndex, 1)
    assert p.surah_names == "الفاتحة"
    assert (p.juz, p.first_ayah, p.last_ayah) == (1, 1, 7)


def test_last_page_closes_the_mushaf(session):
    p = session.get(PageIndex, 604)
    assert p.juz == 30
    assert "الناس" in p.surah_names


def test_multi_surah_page_lists_all_names(session):
    # Page 293 straddles the al-Isra / al-Kahf boundary
    assert session.get(PageIndex, 293).surah_names == "الإسراء، الكهف"


def test_juz_range(session):
    lo, hi = session.execute(select(func.min(PageIndex.juz), func.max(PageIndex.juz))).one()
    assert (lo, hi) == (1, 30)


def test_juz_never_goes_backwards(session):
    juzs = list(session.scalars(select(PageIndex.juz).order_by(PageIndex.page_no)))
    assert all(b >= a for a, b in zip(juzs, juzs[1:], strict=False))


def test_no_empty_ayah_text(session):
    assert session.scalar(select(func.count()).select_from(Ayah).where(Ayah.text == "")) == 0


def test_bom_stripped_from_text(session):
    # The source prefixes some ayahs with a BOM, which breaks display alignment
    bom = session.scalar(select(func.count()).select_from(Ayah).where(Ayah.text.like("﻿%")))
    assert bom == 0


def test_al_fatiha_surah_row(session):
    s = session.get(Surah, 1)
    assert (s.short_name, s.ayah_count, s.first_page, s.last_page) == ("الفاتحة", 7, 1, 1)


def test_al_baqarah_spans_expected_pages(session):
    s = session.get(Surah, 2)
    assert s.ayah_count == 286
    assert (s.first_page, s.last_page) == (2, 49)
