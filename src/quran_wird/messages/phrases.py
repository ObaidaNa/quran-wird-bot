"""The approved Arabic phrase pools.

Every phrase here was reviewed and accepted (see docs/PLAN.md section 7). Three
reviewed phrases were rejected and are deliberately absent; do not re-add them
without asking.

Selection uses a shuffle bag rather than `random.choice`: a bag hands out every
phrase in a pool once before repeating any, so a member never sees the same line
two mornings running. Bags live in memory, so a restart reshuffles — which is
harmless.
"""

from __future__ import annotations

import random
from collections.abc import Hashable, Sequence

# ------------------------------------------------------------ the daily wird

SEND = (
    "﴿وَلَقَدْ يَسَّرْنَا الْقُرْآنَ لِلذِّكْرِ فَهَلْ مِن مُّدَّكِرٍ﴾ — ورد اليوم بين يديك.",
    "«اقرؤوا القرآن؛ فإنه يأتي يوم القيامة شفيعًا لأصحابه» — رواه مسلم.",
    "صفحات اليوم ليست واجبًا يُؤدّى، بل نورٌ يُدَّخر… فابدأ الآن.",
    "﴿إِنَّ الَّذِينَ يَتْلُونَ كِتَابَ اللَّهِ وَأَقَامُوا الصَّلَاةَ… يَرْجُونَ تِجَارَةً لَّن تَبُورَ﴾ [فاطر: ٢٩]",
    "«مَن قرأ حرفًا من كتاب الله فله به حسنة، والحسنة بعشر أمثالها» — رواه الترمذي.",
    "بضع دقائق من يومك… ورصيدٌ لا ينفد في ميزانك.",
    "﴿كِتَابٌ أَنزَلْنَاهُ إِلَيْكَ مُبَارَكٌ لِّيَدَّبَّرُوا آيَاتِهِ﴾ [ص: ٢٩]",
    "اجعل للقرآن موعدًا لا يفوتك… وهذا هو موعد اليوم.",
)

# ---------------------------------------------------------------- reminders

REMINDER_FIRST = (
    "ما زال وردك بانتظارك… ولن يأخذ منك إلا دقائق.",
    "لا تدع يومك يمضي بلا نصيب من كتاب الله.",
    "﴿فَاسْتَبِقُوا الْخَيْرَاتِ﴾ [البقرة: ١٤٨] — سبقك إخوانك، فالحق بهم.",
    "«يُقال لصاحب القرآن: اقرأ وارتقِ ورتّل كما كنت ترتّل في الدنيا، "
    "فإن منزلتك عند آخر آية تقرؤها» — رواه أبو داود والترمذي.",
    "القلوب تصدأ كما يصدأ الحديد، وجلاؤها تلاوة القرآن.",
)

# Only three phrases: two of the reviewed five were rejected. This is the
# smallest pool, but it is only ever seen by members who are already late.
REMINDER_SECOND = (
    "أوشك اليوم على الانقضاء، ووردك لم يكتمل بعد… تدارك.",
    "لا تؤجّل ما تلقاه غدًا في ميزانك.",
    "ساعات قليلة وينتهي اليوم… فلا يفوتنّك نصيبك.",
)

REMINDER_LAST = (
    "سيأتي يومٌ تتمنّى فيه صفحةً واحدة تزيدها في ميزانك… وهي اليوم بين يديك.",
    "﴿وَقَالَ الرَّسُولُ يَا رَبِّ إِنَّ قَوْمِي اتَّخَذُوا هَٰذَا الْقُرْآنَ مَهْجُورًا﴾ [الفرقان: ٣٠] — اللهم لا تجعلنا منهم.",
    "«إن الذي ليس في جوفه شيء من القرآن كالبيت الخَرِب» — رواه الترمذي.",
    "العمر يمضي، ولن يبقى لك إلا ما قدّمت… ووردك اليوم بابٌ مفتوح.",
    "آخر فرصة اليوم… ولا يزال الباب مفتوحًا لمن أراد.",
)

# {n} is replaced with the number of missed days.
MISSED = (
    "فاتك {n} من الأيام… والتدارك اليوم أيسر من الغد.",
    "الانقطاع يبدأ بيومٍ واحد… لا تجعله عادة، وعُد إلى وردك.",
    "مضى عليك {n} يومًا دون ورد — عُد، فباب الله لا يُغلق.",
    "لا تُطِل الهجر؛ فالقرآن يشكو من هجره ﴿اتَّخَذُوا هَٰذَا الْقُرْآنَ مَهْجُورًا﴾.",
)

# ------------------------------------------------------------- completions

DONE = (
    "تقبّل الله منك ونفع بك 🌿",
    "أحسنت! سطر جديد في صحيفتك.",
    "بارك الله فيك… واصل، فالمداومة أحبّ العمل إلى الله.",
    "جعله الله شفيعًا لك يوم القيامة 🤍",
)

ALL_DONE = (
    "🎉 أتمّ الجميع ورد اليوم — «ما اجتمع قومٌ في بيت من بيوت الله يتلون كتاب الله "
    "ويتدارسونه بينهم إلا نزلت عليهم السكينة» — رواه مسلم.",
    "✨ ورد اليوم مكتمل بحمد الله. نسأل الله القبول للجميع.",
)

# ---------------------------------------------------------- weekly report

WEEKLY_CLOSER = (
    "«أحبّ الأعمال إلى الله أدومها وإن قلّ» — متفق عليه.",
    "﴿وَالَّذِينَ جَاهَدُوا فِينَا لَنَهْدِيَنَّهُمْ سُبُلَنَا﴾ [العنكبوت: ٦٩]",
    "المداومة على القليل خيرٌ من الكثير المنقطع… بارك الله في المداومين.",
    "﴿فَاسْتَقِمْ كَمَا أُمِرْتَ﴾ [هود: ١١٢] — أسبوعٌ مضى، وأسبوعٌ يبدأ.",
)

# ------------------------------------------------------------ fixed templates

NUDGE = (
    "بارك الله فيك {name} على قراءتك 🌿\n"
    "لو اشتركت عبر /join سيصلك التذكير، ويُحسب لك التقدّم، وتظهر في لوحة الشرف."
)

WEEK_NOBODY_PERFECT = (
    "لم يكتمل لأحدٍ الأسبوع هذه المرّة… ولا بأس، فالبداية من جديد لا تحتاج إلا نيّة.\n"
    "﴿وَمَن يَتَّقِ اللَّهَ يَجْعَل لَّهُ مَخْرَجًا﴾ — من سيكون في لوحة الشرف الأسبوع القادم؟"
)

WEEK_EVERYONE_PERFECT = (
    "🎉 الأسبوع كامل للجميع بلا استثناء!\n"
    "ما اجتمع قومٌ على كتاب الله إلا نزلت عليهم السكينة وغشيتهم الرحمة."
)

KHATMAH_DONE = (
    "🎉 <b>تمّت الختمة رقم {khatmah} بحمد الله</b>\n"
    "ختمنا كتاب الله في {days} يومًا.\n"
    "وغدًا نبدأ ختمة جديدة بإذن الله ﴿وَقُل رَّبِّ زِدْنِي عِلْمًا﴾."
)

# Sent as its own message right after the khatmah announcement, with parse_mode
# disabled so the tashkeel and line breaks survive intact.
KHATMAH_DUA = """🤲 دعاء ختم القرآن

اللَّهُمَّ ارْحَمْنِي بِالْقُرْآنِ، وَاجْعَلْهُ لِي إِمَامًا وَنُورًا وَهُدًى وَرَحْمَةً.
اللَّهُمَّ ذَكِّرْنِي مِنْهُ مَا نُسِّيتُ، وَعَلِّمْنِي مِنْهُ مَا جَهِلْتُ،
وَارْزُقْنِي تِلَاوَتَهُ آنَاءَ اللَّيْلِ وَأَطْرَافَ النَّهَارِ،
وَاجْعَلْهُ لِي حُجَّةً يَا رَبَّ الْعَالَمِينَ.

اللَّهُمَّ اجْعَلِ الْقُرْآنَ رَبِيعَ قُلُوبِنَا، وَنُورَ صُدُورِنَا،
وَجَلَاءَ أَحْزَانِنَا، وَذَهَابَ هُمُومِنَا وَغُمُومِنَا.

اللَّهُمَّ أَصْلِحْ لَنَا دِينَنَا الَّذِي هُوَ عِصْمَةُ أَمْرِنَا،
وَأَصْلِحْ لَنَا دُنْيَانَا الَّتِي فِيهَا مَعَاشُنَا،
وَأَصْلِحْ لَنَا آخِرَتَنَا الَّتِي فِيهَا مَعَادُنَا،
وَاجْعَلِ الْحَيَاةَ زِيَادَةً لَنَا فِي كُلِّ خَيْرٍ،
وَاجْعَلِ الْمَوْتَ رَاحَةً لَنَا مِنْ كُلِّ شَرٍّ.

اللَّهُمَّ تَقَبَّلْ مِنَّا، إِنَّكَ أَنْتَ السَّمِيعُ الْعَلِيمُ،
وَتُبْ عَلَيْنَا إِنَّكَ أَنْتَ التَّوَّابُ الرَّحِيمُ.
وَصَلَّى اللهُ عَلَى نَبِيِّنَا مُحَمَّدٍ وَعَلَى آلِهِ وَصَحْبِهِ وَسَلَّمَ."""


class ShuffleBag:
    """Hands out every item once, in random order, before repeating any.

    Keyed so each group draws from its own bag; two groups on the same day get
    independent phrases.
    """

    def __init__(self, items: Sequence[str], rng: random.Random | None = None) -> None:
        if not items:
            raise ValueError("a phrase pool cannot be empty")
        self._items = tuple(items)
        self._rng = rng or random.Random()
        self._remaining: dict[Hashable, list[str]] = {}

    def pick(self, key: Hashable = None) -> str:
        bag = self._remaining.get(key)
        if not bag:
            bag = list(self._items)
            self._rng.shuffle(bag)
            self._remaining[key] = bag
        return bag.pop()

    def __len__(self) -> int:
        return len(self._items)


class Phrases:
    """All pools, each with its own bag."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.send = ShuffleBag(SEND, rng)
        self.reminder_first = ShuffleBag(REMINDER_FIRST, rng)
        self.reminder_second = ShuffleBag(REMINDER_SECOND, rng)
        self.reminder_last = ShuffleBag(REMINDER_LAST, rng)
        self.missed = ShuffleBag(MISSED, rng)
        self.done = ShuffleBag(DONE, rng)
        self.all_done = ShuffleBag(ALL_DONE, rng)
        self.weekly_closer = ShuffleBag(WEEKLY_CLOSER, rng)

    def reminder(self, seq: int) -> ShuffleBag:
        """The pool for reminder number `seq` (1-based); later ones reuse the last."""
        return {1: self.reminder_first, 2: self.reminder_second}.get(seq, self.reminder_last)


phrases = Phrases()
