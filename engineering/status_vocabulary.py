"""Единый закрытый словарь статусов деталей, плит и комплектов.

До этого модуля каждый реестр говорил на своём диалекте: PART_STATUS знал
четыре слова, PLATE_STATUS два других, таблица комплектов писала статус
свободной прозой, а манифесты несли девять констант длиной в предложение.
Читатель переводил между ними вручную, а каталог не мог сказать про плиту
«актуальна» или «снята» вообще.

Словарь закрыт нарочно. Новое слово сначала добавляется сюда вместе со
значением, и только потом им может пользоваться реестр, каталог или
комплект: тест ``tests/test_status_vocabulary.py`` проверяет, что каждый
реестр в дереве говорит ровно этими словами.

Две оси, потому что у детали два разных вопроса:

- ``LIFECYCLE`` — актуальна ли она: просят ли её машины, снята ли ревизией.
- ``RELEASE`` — что с ней физически разрешено или уже произошло.

Ни одно слово здесь ничего не разрешает: печать, установку, питание и
движение разрешают только ворота ``MEASUREMENT_AND_BENCH_PLAN.md``.
"""

from __future__ import annotations

#: Актуальность. Выводится каталогом из машин, комплектов, объявлений модулей
#: и записей PART_STATUS / PLATE_STATUS; руками записываются только слова из
#: ``RECORDABLE``.
LIFECYCLE: dict[str, str] = {
    "IN_USE": "деталь или плиту просит хотя бы одна машина или комплект",
    "UNREQUESTED": (
        "существует и записана, но ни одна машина и ни один комплект её не просят"
    ),
    "SUPERSEDED": (
        "снята с обращения позднейшей ревизией; хранится как доказательство "
        "и не перезаписывается"
    ),
    "DIVERGED": (
        "байты у владельца разошлись с записанными; ревизия не переписывается, "
        "расхождение записано с причиной"
    ),
    "COUPON": "измерительный купон для проверки посадки, не деталь изделия",
    "REFERENCE_BODY": "тело-справка для рендеров и сборок, не печатается",
    "SPARE_SIZE": "запасной размер, хранится про запас рядом с рабочим",
    "UNEXPLAINED": (
        "ни одна машина не просит, ни одна плита не несёт, причина нигде не записана"
    ),
}

#: Что физически разрешено или уже произошло. Для плит выводится из манифеста
#: и evidence слайсера, для комплектов объявляется в ``kits/RC_KITS.py``.
RELEASE: dict[str, str] = {
    "INSTALLED": "напечатано и установлено у владельца",
    "SLICED": "нарезано реальным слайсером; печать разрешена только как PRINT_ONLY",
    "PRINT_ONLY": (
        "печать и сухая примерка разрешены; установка, питание и движение нет"
    ),
    "PENDING": "ждёт evidence слайсера или ревью владельца до любой печати",
    "HOLD": (
        "под холдом: геометрия сгенерирована, ни одно физическое действие не разрешено"
    ),
    "NOT_FOR_RELEASE": "отвергнутый или устаревший черновик; хранится, не печатается",
    "VIEW_ONLY": (
        "вид для оценки формы, компоновки и сборочной логики; не результат примерки "
        "и не печатная деталь"
    ),
    "FIT_BLOCKED": (
        "запись sweep (engineering/evidence/**/SWEEP_RESULT.json) доказала, что деталь "
        "нельзя установить на место по пути сборки; печать и примерка не запрещены, "
        "но слово release для гида или комплекта с этой деталью невозможно"
    ),
}

#: Слова, которые человек вправе записать в PART_STATUS.json. IN_USE сюда не
#: входит: актуальность деталь получает от машины, которая её просит, а не от
#: записи. UNEXPLAINED — это отсутствие записи, его нельзя записать.
RECORDABLE: tuple[str, ...] = ("SUPERSEDED", "DIVERGED", "COUPON", "REFERENCE_BODY", "SPARE_SIZE")

#: Слова, которые человек вправе записать в PLATE_STATUS.json.
PLATE_RECORDABLE: tuple[str, ...] = ("SUPERSEDED", "DIVERGED")

#: Не слова словаря, а честные ответы «не знаю»: константа манифеста, которой
#: нет в таблице, и манифест, в котором поля вообще нет.
UNMAPPED = "UNMAPPED"
UNSTATED = "UNSTATED"

#: Порядок показа: сначала то, что нужно живым машинам, в конце — то, что
#: надо разбирать.
LIFECYCLE_RANK: dict[str, int] = {word: index for index, word in enumerate(LIFECYCLE)}

#: Константы, которые манифесты и отчёты пишут в ``authority`` и
#: ``artifact_status``. Таблица закрыта: незнакомая константа отображается в
#: UNMAPPED, и тест по дереву требует добавить её сюда осознанно.
MANIFEST_RELEASE: dict[str, str] = {
    "PRINT_AND_DRY_FIT_ONLY_PENDING_OWNER_REVIEW": "PENDING",
    "PRINT_ONLY_PENDING_USER_SLICER_EVIDENCE": "PENDING",
    "PRINT_BATCH_PLATE_PENDING_USER_SLICER_EVIDENCE": "PENDING",
    "PENDING_USER_EVIDENCE": "PENDING",
    "PRINT_ONLY_DRY_FIT_CANDIDATE": "PRINT_ONLY",
    "PRINT_ONLY_DRY_FIT_ONLY": "PRINT_ONLY",
    "PRINT_ONLY_DRY_FIT_UNPOWERED_CONTINUITY": "PRINT_ONLY",
    "VIEW_ONLY_NOT_A_FIT_RESULT": "VIEW_ONLY",
    "MEASUREMENT_PLATE_CANDIDATE_NOT_FULL_PRINT_RELEASE": "PRINT_ONLY",
    "GENERATED_HOLD": "HOLD",
    "GEOMETRY_CANDIDATE": "HOLD",
    "FAILED_REVIEW_DRAFT_NOT_FOR_RELEASE": "NOT_FOR_RELEASE",
    "REVIEW_SUPERSEDED_INCOMPLETE_INPUT_CLOSURE": "NOT_FOR_RELEASE",
    "REVIEW_SUPERSEDED_EVIDENCE_GAPS": "NOT_FOR_RELEASE",
}


def release_from_manifest(value: object) -> str:
    """Слово ``RELEASE`` для константы манифеста.

    Манифест может уточнять константу через косую черту, например
    ``GEOMETRY_CANDIDATE/GENERATED_HOLD``: тогда решает самая правая известная
    часть, потому что именно она называет текущее ограничение.
    """
    if not isinstance(value, str) or not value.strip():
        return UNSTATED
    if value in MANIFEST_RELEASE:
        return MANIFEST_RELEASE[value]
    for part in reversed(value.split("/")):
        if part in MANIFEST_RELEASE:
            return MANIFEST_RELEASE[part]
    return UNMAPPED


def describe(word: str) -> str:
    """Значение слова любой оси, или пустая строка для слов вне словаря."""
    return LIFECYCLE.get(word) or RELEASE.get(word) or ""
