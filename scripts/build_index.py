#!/usr/bin/env python3
"""يبني فهرس المصحف في قاعدة البيانات من mushaf-content.json داخل hafs.zip.

    uv run scripts/build_index.py

يملأ ثلاثة جداول مرجعية:
  surahs     — 114 سورة: الاسم، عدد الآيات، أول/آخر صفحة
  ayahs      — 6236 آية بنصّها وموضعها (أساس ميزة التفسير مستقبلًا)
  page_index — لكل صفحة: الجزء، الحزب، أول/آخر آية، وأسماء السور الواقعة فيها

page_index هو ما تستعمله رسالة الورد اليومية لتقول
«الصفحات 120–121 · الجزء السادس · النساء».

السكربت idempotent: يمسح الجداول المرجعية ويعيد بناءها، ولا يمسّ بيانات
المجموعات ولا تقدّمها.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

from sqlalchemy import delete, func, select

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

# محرف BOM يتصدّر بعض نصوص الآيات في المصدر ويفسد المحاذاة عند العرض
BOM = "﻿"


def short_name(name: str) -> str:
    """«سورة البقرة» → «البقرة»"""
    return name.removeprefix("سورة ").strip()


def load_content(zip_path: Path) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read(CONTENT_ENTRY))


def build_rows(surahs: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """يحوّل شجرة JSON إلى صفوف الجداول الثلاثة."""
    surah_rows: list[dict] = []
    ayah_rows: list[dict] = []
    # ترتيب المصحف = ترتيب السور ثم الآيات كما وردت في المصدر
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
        names = list(dict.fromkeys(e[2] for e in entries))  # فريدة بترتيب الورود
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
        (Surah, TOTAL_SURAHS, "عدد السور"),
        (Ayah, TOTAL_AYAHS, "عدد الآيات"),
        (PageIndex, TOTAL_PAGES, "عدد الصفحات"),
    ]
    for model, expected, label in counts:
        got = session.scalar(select(func.count()).select_from(model))
        if got != expected:
            problems.append(f"{label}: توقّعنا {expected} ووجدنا {got}")

    present = set(session.scalars(select(PageIndex.page_no)))
    missing = sorted(set(range(1, TOTAL_PAGES + 1)) - present)
    if missing:
        problems.append(f"صفحات مفقودة: {missing[:10]}… ({len(missing)})")

    juz_range = session.execute(select(func.min(PageIndex.juz), func.max(PageIndex.juz))).one()
    if juz_range != (1, 30):
        problems.append(f"مدى الأجزاء غير صحيح: {tuple(juz_range)}")

    empty = session.scalar(select(func.count()).select_from(Ayah).where(Ayah.text == ""))
    if empty:
        problems.append(f"{empty} آية بنصّ فارغ")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="بناء فهرس المصحف")
    ap.add_argument("--zip", type=Path, default=DEFAULT_ZIP)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    if not args.zip.exists():
        sys.exit(f"الأرشيف غير موجود: {args.zip}")

    print(f"المصدر : {args.zip}")
    print(f"القاعدة: {args.db}\n")

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
            print(f"  السور   : {len(surah_rows)}")
            print(f"  الآيات  : {len(ayah_rows)}")
            print(f"  الصفحات : {len(page_rows)}")

            problems = verify(session)
            if problems:
                print("\nمشاكل في التحقّق:", file=sys.stderr)
                for p in problems:
                    print(f"  ✗ {p}", file=sys.stderr)
                return 1

            print("\n✓ التحقّق تمّ: 114 سورة · 6236 آية · 604 صفحة · الأجزاء 1–30")
            print("\nعيّنة:")
            sample = session.scalars(
                select(PageIndex)
                .where(PageIndex.page_no.in_([1, 2, 120, 293, 604]))
                .order_by(PageIndex.page_no)
            )
            for r in sample:
                print(
                    f"  صفحة {r.page_no:>3} · الجزء {r.juz:>2} · {r.surah_names} "
                    f"(الآيات {r.first_ayah}–{r.last_ayah})"
                )
    finally:
        engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
