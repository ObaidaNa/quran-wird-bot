"""الأساس المشترك لنماذج SQLAlchemy."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# تسمية موحّدة للقيود — بدونها تولّد SQLite قيودًا بلا أسماء فيعجز Alembic
# عن تعديلها لاحقًا (وهذا مهم بوجه خاص لأن SQLite يعيد بناء الجدول عند أي تغيير).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
