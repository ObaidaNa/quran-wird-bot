#!/usr/bin/env python3
"""Build the mushaf reference index from mushaf-content.json inside hafs.zip.

The archive is downloaded from Quranpedia on first use if it is not already in
assets/; see scripts/source_archive.py.

    uv run scripts/build_index.py

Fills three reference tables:
  surahs     - 114 surahs: name, ayah count, first/last page
  ayahs      - 6236 ayahs with text and position (basis for the tafsir feature)
  page_index - per page: juz, hizb, first/last ayah, and the surahs it spans

page_index is what lets the daily wird message say
"pages 120-121 - juz 6 - an-Nisa".

Idempotent: it clears and rebuilds the reference tables only, never touching
group data or khatmah progress.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from sqlalchemy import delete, func, select

sys.path.insert(0, str(Path(__file__).resolve().parent))

from source_archive import SOURCE_URL, ensure  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from quran_wird.db.models import Ayah, PageIndex, Surah  # noqa: E402
from quran_wird.db.session import (  # noqa: E402
    create_engine_sync,
    run_migrations,
    sync_session_scope,
)

DEFAULT_ZIP = PROJECT_ROOT / "assets" / "hafs.zip"
DEFAULT_DB = PROJECT_ROOT / "data" / "bot.db"
CONTENT_ENTRY = "mushaf-content.json"

TOTAL_PAGES = 604
TOTAL_AYAHS = 6236
TOTAL_SURAHS = 114

# The source prefixes some ayah texts with a BOM, which breaks display alignment
BOM = "﻿"


def short_name(name: str) -> str:
    """Strip the leading word: "سورة البقرة" -> "البقرة"."""
    return name.removeprefix("سورة ").strip()


def load_content(zip_path: Path) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read(CONTENT_ENTRY))


def build_rows(surahs: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Turn the JSON tree into rows for the three reference tables."""
    surah_rows: list[dict] = []
    ayah_rows: list[dict] = []
    # Mushaf order = surahs in order, then ayahs in order, as the source lists them
    per_page: dict[int, list[tuple[int, int, str]]] = {}
    page_meta: dict[int, tuple[int, int]] = {}

    for surah in surahs:
        number = int(surah["id"])
        name = surah["name"].strip()
        ayahs = surah["ayahs"]
        pages = [int(a["page_number"]) for a in ayahs]

        surah_rows.append(
            {
                "number": number,
                "name": name,
                "short_name": short_name(name),
                "ayah_count": len(ayahs),
                "first_page": min(pages),
                "last_page": max(pages),
            }
        )

        for a in ayahs:
            page = int(a["page_number"])
            juz, hizb = int(a["juz"]), int(a["hizb"])
            ayah_rows.append(
                {
                    "surah": number,
                    "number": int(a["number"]),
                    "page_no": page,
                    "juz": juz,
                    "hizb": hizb,
                    "text": a["text"].replace(BOM, "").strip(),
                }
            )
            per_page.setdefault(page, []).append((number, int(a["number"]), short_name(name)))
            page_meta.setdefault(page, (juz, hizb))

    page_rows: list[dict] = []
    for page in sorted(per_page):
        entries = per_page[page]
        juz, hizb = page_meta[page]
        first_surah, first_ayah, _ = entries[0]
        last_surah, last_ayah, _ = entries[-1]
        names = list(dict.fromkeys(e[2] for e in entries))  # unique, in order of appearance
        page_rows.append(
            {
                "page_no": page,
                "juz": juz,
                "hizb": hizb,
                "first_surah": first_surah,
                "first_ayah": first_ayah,
                "last_surah": last_surah,
                "last_ayah": last_ayah,
                "surah_names": "، ".join(names),
                "ayah_count": len(entries),
            }
        )

    return surah_rows, ayah_rows, page_rows


def verify(session) -> list[str]:
    problems: list[str] = []

    counts = [
        (Surah, TOTAL_SURAHS, "surah count"),
        (Ayah, TOTAL_AYAHS, "ayah count"),
        (PageIndex, TOTAL_PAGES, "page count"),
    ]
    for model, expected, label in counts:
        got = session.scalar(select(func.count()).select_from(model))
        if got != expected:
            problems.append(f"{label}: expected {expected}, found {got}")

    present = set(session.scalars(select(PageIndex.page_no)))
    missing = sorted(set(range(1, TOTAL_PAGES + 1)) - present)
    if missing:
        problems.append(f"missing pages: {missing[:10]}... ({len(missing)})")

    juz_range = session.execute(select(func.min(PageIndex.juz), func.max(PageIndex.juz))).one()
    if juz_range != (1, 30):
        problems.append(f"unexpected juz range: {tuple(juz_range)}")

    empty = session.scalar(select(func.count()).select_from(Ayah).where(Ayah.text == ""))
    if empty:
        problems.append(f"{empty} ayahs have empty text")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the mushaf reference index")
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    ap.add_argument(
        "--no-download",
        action="store_true",
        help=f"fail instead of fetching the archive from {SOURCE_URL}",
    )
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    args.zip = ensure(args.zip, allow_download=not args.no_download)

    print(f"source  : {args.zip}")
    print(f"database: {args.db}\n")

    run_migrations(args.db)
    surah_rows, ayah_rows, page_rows = build_rows(load_content(args.zip))

    engine = create_engine_sync(args.db)
    try:
        with sync_session_scope(engine) as session:
            session.execute(delete(Ayah))
            session.execute(delete(PageIndex))
            session.execute(delete(Surah))
            session.bulk_insert_mappings(Surah, surah_rows)
            session.bulk_insert_mappings(Ayah, ayah_rows)
            session.bulk_insert_mappings(PageIndex, page_rows)

        with sync_session_scope(engine) as session:
            print(f"  surahs : {len(surah_rows)}")
            print(f"  ayahs  : {len(ayah_rows)}")
            print(f"  pages  : {len(page_rows)}")

            problems = verify(session)
            if problems:
                print("\nverification problems:", file=sys.stderr)
                for p in problems:
                    print(f"  ✗ {p}", file=sys.stderr)
                return 1

            print("\n✓ verified: 114 surahs, 6236 ayahs, 604 pages, juz 1-30")
            print("\nsample:")
            sample = session.scalars(
                select(PageIndex)
                .where(PageIndex.page_no.in_([1, 2, 120, 293, 604]))
                .order_by(PageIndex.page_no)
            )
            for r in sample:
                print(
                    f"  page {r.page_no:>3} | juz {r.juz:>2} | {r.surah_names} "
                    f"(ayahs {r.first_ayah}-{r.last_ayah})"
                )
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
