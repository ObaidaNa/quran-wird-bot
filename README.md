# بوت الورد اليومي — Quran Wird Bot

بوت تيليجرام عربي يرسل وردًا يوميًا من صفحات المصحف في المجموعات، يتابع من أتمّ
القراءة ومن لم يُتمّ، ويذكّر المتأخّرين بعبارات محفّزة، ويحفظ تقدّم المجموعة حتى
ختم المصحف كاملًا (604 صفحات).

**البوت لك:** الشيفرة لا ترتبط باسم بوت بعينه — أنشئ بوتك من
[@BotFather](https://t.me/BotFather) بالاسم الذي تريد، وضع توكنه في `.env`،
وهذا كل شيء.

الخطة الكاملة والقرارات المعتمدة في [`docs/PLAN.md`](docs/PLAN.md).

<details>
<summary><b>In English</b></summary>

An Arabic Telegram bot that posts a daily Quran reading (a *wird*) as mushaf page
images into a group, tracks who has finished, mentions latecomers with
encouraging phrases, and carries the group through a shared 604-page khatmah.

Runs one bot for many groups; each group keeps its own pace, timezone, schedule
and progress, all configurable from an in-group button panel. Bring your own bot
token — nothing here is tied to a particular bot.

Python 3.11+ · python-telegram-bot · SQLAlchemy 2 async + Alembic · SQLite ·
managed with `uv`. All user-facing text is Arabic and lives in
`src/quran_wird/messages/`; code and comments are in English. The documentation
below is Arabic, since it is written for the people who run the bot.

</details>

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

**١. أنشئ بوتك:** أرسل `/newbot` إلى [@BotFather](https://t.me/BotFather)، واختر
له الاسم والمعرّف اللذين تريد، واحتفظ بالتوكن. ثم `/setprivacy` → **Enable**:
البوت لا يحتاج قراءة رسائل المجموعة، تكفيه الأوامر والأزرار.

**٢. ثبّت المشروع:**

```bash
uv sync                       # تنصيب الاعتماديات
cp .env.example .env          # ثم ضع BOT_TOKEN في .env
```

قائمة الأوامر في تيليجرام تُضبط تلقائيًا عند أول تشغيل، فلا حاجة إلى
`/setcommands`.

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
| `BACKUP_ENABLED` | — | `true` | نسخة احتياطية ليلية من قاعدة البيانات |
| `BACKUP_DIR` | — | `data/backups` | مجلد النسخ الاحتياطية |
| `BACKUP_KEEP` | — | `7` | كم نسخة تُحفظ قبل حذف الأقدم |
| `BACKUP_TIME` | — | `03:30` | موعد النسخ بـ `DEFAULT_TIMEZONE` |
| `WEBHOOK_URL` | — | *(فارغ)* | **مفتاح التبديل** — العنوان العام، مثل `https://bot.example.com` |
| `SECRET_TOKEN` | webhook | — | يتحقّق منه تيليجرام عبر `X-Telegram-Bot-Api-Secret-Token` |
| `PORT` | webhook | `8443` | منفذ الاستماع المحلي |
| `URL_PATH` | webhook | *(التوكن)* | مسار الـ endpoint |

## النشر على خادم

الوضع المعتمد: **VPS مع وحدة `systemd`**. البوت يعمل بالـ long polling افتراضيًا،
فلا يحتاج عنوانًا عامًّا ولا شهادة ولا منفذًا مفتوحًا.

### 1. تجهيز الخادم

```bash
sudo useradd --system --create-home --home-dir /opt/quran-wird quranwird
sudo -u quranwird git clone <repo> /opt/quran-wird
cd /opt/quran-wird
sudo -u quranwird uv sync --frozen
```

### 2. الإعدادات وصور المصحف

```bash
sudo -u quranwird cp .env.example .env
sudo -u quranwird nano .env          # ضع BOT_TOKEN
sudo chmod 600 .env                  # التوكن سرّ

sudo -u quranwird uv run scripts/build_index.py
sudo -u quranwird uv run scripts/build_pages.py
```

بناء الصور يحتاج `librsvg` و `ImageMagick`، ويأخذ بضع دقائق مرّة واحدة. وإن كانت
الصور مبنيّة على جهازك فانقلها بدل بنائها من جديد:

```bash
rsync -a assets/pages/ quranwird@server:/opt/quran-wird/assets/pages/
```

### 3. تشغيل الخدمة

```bash
sudo cp deploy/quran-wird.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quran-wird
```

`Restart=always` مع `StartLimitIntervalSec=0`: البوت **لا يحتفظ بحالة في
الذاكرة** — كل المهام تُعاد بناؤها من قاعدة البيانات عند الإقلاع، وكل ما لا يجوز
تكراره محروس بصفّ في قاعدة البيانات (الورد المُرسل، التذكير المُسجَّل، اليوم
المُغلَق، التقرير الأسبوعي). لذلك إعادة التشغيل آمنة دائمًا، وانقطاع تيليجرام
ساعاتٍ لا يجوز أن يترك البوت متوقّفًا بعد عودته.

### المتابعة

```bash
sudo systemctl status quran-wird
sudo journalctl -u quran-wird -f            # السجلّ الحيّ
sudo journalctl -u quran-wird --since today
```

عند الإقلاع يسجّل البوت سطرًا لكل مجموعة بمواعيدها، فسطر واحد يكشف أن الجدولة
أُعيد بناؤها كما ينبغي.

### التحديث

```bash
cd /opt/quran-wird
sudo -u quranwird git pull
sudo -u quranwird uv sync --frozen
sudo -u quranwird uv run scripts/backup.py    # نسخة قبل الهجرة
sudo systemctl restart quran-wird             # الهجرات تعمل تلقائيًا عند الإقلاع
```

### النسخ الاحتياطي والاستعادة

البوت ينسخ قاعدته كل ليلة في `BACKUP_TIME` بواجهة SQLite للنسخ الحيّ (لا `cp`،
فهي قد تلتقط كتابة نصف مكتملة)، ويحتفظ بآخر `BACKUP_KEEP` نسخة.

```bash
uv run scripts/backup.py            # نسخة الآن
uv run scripts/backup.py --list     # ما هو موجود
uv run scripts/backup.py /mnt/usb   # إلى مكان آخر
```

الاستعادة نسخُ ملف مكان الملف:

```bash
sudo systemctl stop quran-wird
sudo -u quranwird cp data/backups/bot-20260906-033000.db data/bot.db
sudo systemctl start quran-wird
```

الملف كلّه بضع مئات من الكيلوبايتات، فانسخه إلى خارج الخادم من حين لآخر — فيه
تقدّم كل مجموعة وسلاسل كل عضو، وهو وحده ما لا يمكن إعادة بنائه.

### وضع الـ webhook (اختياري)

اضبط `WEBHOOK_URL` فيتحوّل البوت تلقائيًا. يحتاج عندها عكس وكيل (nginx أو Caddy)
بشهادة TLS يمرّر إلى `PORT`، مع `SECRET_TOKEN` ليتحقّق البوت أن الطلب من تيليجرام.
الـ long polling يكفي لمجموعات بالعشرات، ولا يحتاج شيئًا من هذا.

### Docker (اختياري)

```bash
docker build -t quran-wird .
docker run -d --name quran-wird --restart unless-stopped \
  --env-file .env \
  -v "$PWD/data:/app/data" \
  -v "$PWD/assets/pages:/app/assets/pages:ro" \
  quran-wird
```

صور الصفحات لا تُبنى داخل الصورة (‏604 صورة ‎~106 MB‏) بل تُركَّب من الخارج.

---

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
└── tg/             مساعدات المنشن والوسائط وكاش file_id، والتصرّف عند الطرد

deploy/quran-wird.service   وحدة systemd
scripts/backup.py           نسخة احتياطية يدوية
Dockerfile                  بديل اختياري للنشر
```

`domain/` لا يستورد `telegram` إطلاقًا — لذلك يُختبر كلّه بلا شبكة.

## المصدر

صور المصحف وبياناته من رواية حفص في `assets/hafs.zip`: 604 صفحة SVG، وملف
مواضع آيات لكل صفحة، و `mushaf-content.json` بنصّ المصحف كاملًا مع الجزء والحزب
ورقم الصفحة لكل آية.

الملف غير مرفوع مع المستودع لحجمه (91 MB)، وكذلك الصور المبنيّة منه. من أراد
تشغيل المشروع فليضع نسخته في `assets/hafs.zip` ثم يشغّل سكربتَي البناء.

---

## المساهمة

المساهمات مرحّب بها. الطريق المعتاد: افتح مسألة (issue) لما هو كبير، ثم فرعًا
وطلب دمج.

**قبل طلب الدمج:**

```bash
uv run ruff check --fix . && uv run ruff format . && uv run pytest -q
```

**خمس قواعد تحفظ شكل المشروع:**

1. **التعليقات وسلاسل التوثيق بالإنجليزية**، والنصوص التي يراها المستخدم
   بالعربية. العربية في الشيفرة موضعها البيانات (أسماء السور مثلًا) لا الشرح.
2. **كل نصّ يظهر للمستخدم في `messages/`** — لا تكتب جملة عربية داخل معالج أو
   مهمّة. `ar.py` للرسائل الكاملة، و `phrases.py` لمجمّعات العبارات، وملفات
   `*_view.py` و `render.py` للتركيب.
3. **`domain/` لا يستورد `telegram` إطلاقًا**، وكل وصول إلى قاعدة البيانات يمرّ
   عبر `db/repo/`. هذا ما يجعل المنطق قابلًا للاختبار بلا شبكة.
4. **الاختبارات لا تلمس الشبكة** — `tests/fakes.py` فيه بوت وهمي وجلسات وهمية.
   واكتب اسم الاختبار واصفًا للسلوك وسببه، لا للدالة.
5. **العبارات العربية الجديدة تُراجَع أوّلًا.** المجمّعات في `phrases.py` مُراجَعة
   ومعتمدة (القسم 7 من الخطة)؛ لإضافة عبارة أو تعديلها افتح مسألة قبل الشيفرة.

**عند تغيير نماذج قاعدة البيانات:** ولّد هجرة وراجعها بنفسك قبل الدمج —
`alembic` لا يضيف `server_default` تلقائيًا، وعمود `NOT NULL` بلا قيمة افتراضية
يُفشل الترقية على قاعدة فيها بيانات.

```bash
uv run alembic revision --autogenerate -m "وصف التغيير"
uv run alembic upgrade head
```

**رسائل الـ commit:** سطر أول موجز، ثم فقرة تشرح *لماذا* لا *ماذا* — التغيير
نفسه يظهر في الفرق.

## الرخصة

_(لم تُحدَّد بعد — أضِف ملف `LICENSE` قبل النشر.)_

نصوص القرآن وصوره في `assets/` لها أحكامها ومصادرها، وهي ليست جزءًا من رخصة
الشيفرة.
