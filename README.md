# وِرْدُنا — Quran Wird Bot

**البوت:** [@WirdunaBot](https://t.me/WirdunaBot) · الاسم الظاهر: **وِرْدُنا**

بوت تيليجرام عربي يرسل وردًا يوميًا من صفحات المصحف في المجموعات، يتابع من أتمّ
القراءة ومن لم يُتمّ، ويذكّر المتأخّرين بعبارات محفّزة، ويحفظ تقدّم المجموعة حتى
ختم المصحف كاملًا (604 صفحات).

الخطة الكاملة والقرارات المعتمدة في [`docs/PLAN.md`](docs/PLAN.md).

## المتطلّبات

- Python 3.11+ و [`uv`](https://docs.astral.sh/uv/)
- `librsvg` و `ImageMagick` — لبناء صور الصفحات مرة واحدة فقط

```bash
# Arch
sudo pacman -S librsvg imagemagick
# Debian / Ubuntu
sudo apt install librsvg2-bin imagemagick
```

## التنصيب

```bash
uv sync                       # تنصيب الاعتماديات
cp .env.example .env          # ثم ضع BOT_TOKEN في .env
```

## البناء لمرة واحدة

```bash
uv run scripts/build_index.py   # فهرس المصحف: 114 سورة · 6236 آية · 604 صفحة
uv run scripts/build_pages.py   # صور الصفحات: 604 صورة · ~106 MB
```

`build_pages.py` يحوّل ملفات SVG في `assets/hafs.zip` إلى صور PNG بمقاس موحّد
1240×1977. الصفحتان 1 و 2 مربّعتان في المصدر فتُوسَّطان على لوحة بيضاء بنفس نسبة
باقي الصفحات، وإلا بدا الألبوم مشوّهًا. الصور رمادية بالكامل في المصدر فتُكمَّم
إلى 32 درجة رمادية: ~180 KB للصفحة بلا خسارة مرئية.

كلا السكربتين idempotent — أعِد تشغيلهما بأمان. `--force` يعيد بناء الصور الموجودة.

## التشغيل

```bash
uv run -m quran_wird
```

الوضع يُختار تلقائيًا: **إن كان `WEBHOOK_URL` مضبوطًا يعمل بالـ webhook، وإلا
long polling.**

| المتغيّر | مطلوب | الافتراضي | الوصف |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | توكن البوت من BotFather |
| `DB_PATH` | — | `data/bot.db` | مسار قاعدة SQLite |
| `PAGES_DIR` | — | `assets/pages` | مجلد صور الصفحات |
| `DEFAULT_TIMEZONE` | — | `Asia/Damascus` | المنطقة الزمنية لأي مجموعة جديدة |
| `LOG_LEVEL` | — | `INFO` | مستوى السجلّات |
| `CONNECT_TIMEOUT` | — | `30` | مهلة الاتصال بالثواني — ارفعها إن كان الوصول إلى تيليجرام بطيئًا |
| `READ_TIMEOUT` | — | `30` | مهلة القراءة بالثواني |
| `WEBHOOK_URL` | — | *(فارغ)* | **مفتاح التبديل** — العنوان العام، مثل `https://bot.example.com` |
| `SECRET_TOKEN` | webhook | — | يتحقّق منه تيليجرام عبر `X-Telegram-Bot-Api-Secret-Token` |
| `PORT` | webhook | `8443` | منفذ الاستماع المحلي |
| `URL_PATH` | webhook | *(التوكن)* | مسار الـ endpoint |

## التطوير

```bash
uv run pytest                 # الاختبارات
uv run ruff check .           # الفحص
uv run ruff format .          # التنسيق
```

### الهجرات

```bash
uv run alembic revision --autogenerate -m "وصف التغيير"
uv run alembic upgrade head
```

الهجرات تعمل بـ `render_as_batch=True` لأن SQLite لا يدعم `ALTER TABLE` الكامل،
فيعيد Alembic بناء الجدول بأمان عند تعديل عمود.

## البنية

```
src/quran_wird/
├── config.py       إعدادات البيئة (pydantic-settings)
├── db/             نماذج SQLAlchemy والجلسات
├── domain/         منطق خالٍ من تيليجرام: مخطّطات Pydantic، الجدولة، التقدّم
├── content/        مزوّدو المحتوى — صفحات المصحف اليوم، والتفسير/الأذكار لاحقًا
├── messages/       كل النصوص العربية ومجمّعات العبارات
├── handlers/       أوامر تيليجرام وأزراره
├── jobs/           المهام المجدولة: الإرسال، التذكير، إغلاق اليوم، التقرير الأسبوعي
└── tg/             مساعدات المنشن والوسائط وكاش file_id
```

`domain/` لا يستورد `telegram` إطلاقًا — لذلك يُختبر كلّه بلا شبكة.

## المصدر

صور المصحف وبياناته من رواية حفص في `assets/hafs.zip`: 604 صفحة SVG، وملف
مواضع آيات لكل صفحة، و `mushaf-content.json` بنصّ المصحف كاملًا مع الجزء والحزب
ورقم الصفحة لكل آية.
