"""Замыкание по фактам: кандидат пинит потреблённые факты, а не байты манифеста.

Побайтовый пин ``cad/prototype_parameters.json`` был верной идеей с неверным
зерном: одно измерение, записанное где угодно в манифесте, обесценивало все
кандидаты, когда-либо читавшие файл, включая те, что изменённый факт не
использовали. Замыкание сравнивает ровно те записи, которые кандидат
потребил, между образом, из которого он был сгенерирован, и рабочим деревом,
и называет, какая из них сдвинулась.

Модуль чист: ни git, ни реестра он не знает, кроме одной вспомогательной
функции чтения blob, которой пользуются реестр и тесты. Никакого нового
authority он не добавляет: что именно потребил кандидат, объявляет его
модуль параметров (``CONSUMED_FACT_IDS``), а реестр это записывает.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable

#: Поля записи, которые читает ``engineering.facts.FactManifest.resolve``.
#: Замыкание обязано сравнивать тот же набор: изменение, которое увидел бы
#: resolver, не имеет права пройти замыкание. Примечания и комментарии рядом
#: с числом — документация, а не факт.
COMPARED_FIELDS: tuple[str, ...] = (
    "value",
    "unit",
    "provenance",
    "source",
    "datum",
    "basis",
    "scope",
    "revision",
)

TABLE_KEY = "parameters"


def fact_table(payload: bytes) -> dict[str, Any]:
    """Таблица фактов из байтов манифеста; ``ValueError`` для всего, что не она."""
    try:
        data = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"fact manifest is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("fact manifest must be a JSON object")
    table = data.get(TABLE_KEY)
    if not isinstance(table, dict):
        raise ValueError(f"fact manifest has no {TABLE_KEY!r} object")
    return table


def fact_record(table: dict[str, Any], fact_id: str) -> dict[str, Any] | None:
    """Сравниваемая часть записи факта, или ``None``, если записи нет."""
    record = table.get(fact_id)
    if not isinstance(record, dict):
        return None
    return {field: record.get(field) for field in COMPARED_FIELDS}


def record_closure(payload: bytes, fact_ids: Iterable[str]) -> dict[str, Any]:
    """Замыкание для записи в доказательство: сравниваемая часть каждого потреблённого факта.

    Хранит записи, а не образ манифеста: доказательству не нужен весь файл, чтобы
    позже ответить, сдвинулось ли то, что оно читало.
    """
    ids = tuple(fact_ids)
    if not ids:
        raise ValueError("fact_closure lists no facts")
    if len(set(ids)) != len(ids):
        raise ValueError("fact_closure repeats a fact id")
    table = fact_table(payload)
    return {fact_id: fact_record(table, fact_id) for fact_id in sorted(ids)}


def closure_drift(closure: dict[str, Any], current: bytes) -> list[str]:
    """Что из записанного замыкания разошлось с текущим манифестом.

    Пустой список означает, что ни один потреблённый факт не сдвинулся; изменение
    любого другого места файла сюда не попадает и попадать не должно. Пустое или
    нечитаемое замыкание — проблема: замыкание без фактов ничего не замыкает.
    """
    if not isinstance(closure, dict) or not closure:
        return ["fact_closure lists no facts"]
    try:
        table = fact_table(current)
    except ValueError as exc:
        return [f"working tree image unreadable: {exc}"]
    problems = []
    for fact_id, recorded in sorted(closure.items()):
        if not isinstance(recorded, dict):
            problems.append(f"{fact_id}: recorded closure entry is not a record")
            continue
        now = fact_record(table, fact_id)
        if now is None:
            problems.append(f"{fact_id}: absent from the fact manifest")
            continue
        moved = [f for f in COMPARED_FIELDS if recorded.get(f) != now.get(f)]
        if moved:
            problems.append(f"{fact_id}: " + ", ".join(
                f"{f} {recorded.get(f)!r} -> {now.get(f)!r}" for f in moved))
    return problems


#: Поля, без которых запись факта не описывает расчёт: их отсутствие в
#: доказательстве — отказ, а не повод сравнивать остальное.
DECISIVE_FIELDS: tuple[str, ...] = ("value", "unit", "provenance")


def verify_recorded_facts(recorded: Any, current: bytes, *, declared_ids: Iterable[str] | None = None,
                          unrecorded_ok: Iterable[str] = ()) -> list[str]:
    """Сверить факты, которые доказательство само записало, с текущим манифестом.

    Отвечает на вопрос «сдвинулось ли то, от чего этот артефакт зависит», по
    тому, что в нём уже лежит: ни снимка прежнего манифеста, ни повторного
    прогона исследования не требуется. Изменение любого другого места файла
    сюда не попадает и попадать не должно.

    Fail closed. Пустой набор ничего не замыкает. Набор, расходящийся с тем, что
    производящий код объявил потреблённым (``declared_ids``), означает, что
    доказательство уже кода, и тогда сверка пропустит изменение, которое обязана
    отклонить. Запись без ``DECISIVE_FIELDS`` не описывает расчёт. Сравниваемое
    поле, которого производитель не записал, **сравнить нельзя**: считать
    «не записано» за «было пусто» — значит пропустить факт, у которого это поле
    было и исчезло. Такие поля перечисляются в ``unrecorded_ok`` на месте
    вызова, чтобы пробел был объявлен, а не растворился.
    """
    if not isinstance(recorded, dict) or not recorded:
        return ["fact_closure lists no facts"]
    problems: list[str] = []
    if declared_ids is not None:
        declared = set(declared_ids)
        missing = sorted(declared - set(recorded))
        extra = sorted(set(recorded) - declared)
        if missing:
            problems.append("recorded facts omit what the code declares it consumes: " + ", ".join(missing))
        if extra:
            problems.append("recorded facts include what the code does not declare: " + ", ".join(extra))
        if problems:
            return problems
    try:
        table = fact_table(current)
    except ValueError as exc:
        return [f"working tree image unreadable: {exc}"]
    skip = set(unrecorded_ok)
    for fact_id, entry in sorted(recorded.items()):
        if not isinstance(entry, dict):
            problems.append(f"{fact_id}: recorded entry is not a fact record")
            continue
        absent = [f for f in DECISIVE_FIELDS if f not in entry]
        if absent:
            problems.append(f"{fact_id}: recorded entry omits {', '.join(absent)}")
            continue
        unrecorded = [f for f in COMPARED_FIELDS if f not in entry and f not in skip]
        if unrecorded:
            problems.append(f"{fact_id}: {', '.join(unrecorded)} never recorded, so it cannot be compared")
            continue
        now = fact_record(table, fact_id)
        if now is None:
            problems.append(f"{fact_id}: absent from the fact manifest")
            continue
        moved = [f for f in COMPARED_FIELDS if f in entry and entry.get(f) != now.get(f)]
        if moved:
            problems.append(f"{fact_id}: " + ", ".join(
                f"{f} {entry.get(f)!r} -> {now.get(f)!r}" for f in moved))
    return problems


#: Поля, которые ``engineering.facts.FactResolution`` отдаёт из манифеста.
#: ``status`` и ``detail`` в набор не входят: они отвечают, что потребитель
#: потребовал от факта (единицу, происхождение, область), а не что записано в
#: манифесте. Факт, потерявший значение, всё равно не пройдёт: resolver тогда
#: не отдаёт ни значения, ни остальных полей.
RESOLVED_FIELDS: tuple[str, ...] = ("value", "unit", "provenance", "source", "scope", "datum")


def resolved_record(table: dict[str, Any], fact_id: str) -> dict[str, Any]:
    """Разрешённая запись факта — тем же resolver'ом, который её и породил.

    Своей копии правил разрешения здесь нет: ``scope`` с запасным ``revision`` и
    ``datum`` с запасным ``basis`` живут в ``engineering.facts``, и повторять их
    тут значило бы завести второе место, где они могут разойтись.
    """
    from engineering.facts import FactManifest

    row = FactManifest({"parameters": table}).resolve(fact_id).as_dict()
    return {field: row[field] for field in RESOLVED_FIELDS}


def verify_resolved_facts(recorded: Any, current: bytes, *,
                          declared_ids: Iterable[str] | None = None) -> list[str]:
    """Сверить разрешённые записи фактов, которые доказательство само сохранило.

    Большая часть доказательств в дереве записала не сырую запись манифеста, а
    то, что вернул ``FactManifest.resolve``. В этой форме две пары альтернатив
    уже схлопнуты: ``scope`` вобрал ``revision``, ``datum`` вобрал ``basis``.
    Сверять такую запись с сырой записью нельзя — расхождение было бы объявлено
    там, где ничего не двигалось, — поэтому сравнение идёт через тот же resolver.
    Строгости это не снимает: любое сырое поле, которое resolver читает,
    меняет и разрешённое значение.

    Fail closed, как и у ``verify_recorded_facts``: пустой набор ничего не
    замыкает, набор, разошедшийся с объявленным в коде, означает, что
    доказательство уже кода, а неполная запись не описывает расчёт. Списка
    «этого производитель не пишет» здесь нет и быть не может: набор полей
    разрешённой формы фиксирован, поэтому пропуск — всегда дефект.
    """
    if not isinstance(recorded, dict) or not recorded:
        return ["fact_closure lists no facts"]
    problems: list[str] = []
    if declared_ids is not None:
        declared = set(declared_ids)
        missing = sorted(declared - set(recorded))
        extra = sorted(set(recorded) - declared)
        if missing:
            problems.append("recorded facts omit what the code declares it consumes: " + ", ".join(missing))
        if extra:
            problems.append("recorded facts include what the code does not declare: " + ", ".join(extra))
        if problems:
            return problems
    try:
        table = fact_table(current)
    except ValueError as exc:
        return [f"working tree image unreadable: {exc}"]
    for fact_id, entry in sorted(recorded.items()):
        if not isinstance(entry, dict):
            problems.append(f"{fact_id}: recorded entry is not a fact record")
            continue
        absent = [f for f in RESOLVED_FIELDS if f not in entry]
        if absent:
            problems.append(f"{fact_id}: recorded entry omits {', '.join(absent)}")
            continue
        if fact_id not in table:
            problems.append(f"{fact_id}: absent from the fact manifest")
            continue
        now = resolved_record(table, fact_id)
        moved = [f for f in RESOLVED_FIELDS if entry.get(f) != now.get(f)]
        if moved:
            problems.append(f"{fact_id}: " + ", ".join(
                f"{f} {entry.get(f)!r} -> {now.get(f)!r}" for f in moved))
    return problems


def closure_problems(pinned: bytes, current: bytes, fact_ids: Iterable[str]) -> list[str]:
    """Чем потреблённые факты в ``current`` отличаются от ``pinned``.

    Пустой список означает, что каждый названный факт идентичен по всем
    ``COMPARED_FIELDS``. Всё остальное — проблема, включая нечитаемый образ и
    пустой список фактов: замыкание без фактов ничего не замыкает.
    """
    ids = tuple(fact_ids)
    if not ids:
        return ["fact_closure lists no facts"]
    try:
        pinned_table = fact_table(pinned)
    except ValueError as exc:
        return [f"pinned image unreadable: {exc}"]
    try:
        current_table = fact_table(current)
    except ValueError as exc:
        return [f"working tree image unreadable: {exc}"]

    problems: list[str] = []
    for fact_id in ids:
        before = fact_record(pinned_table, fact_id)
        after = fact_record(current_table, fact_id)
        if before is None:
            problems.append(f"consumed fact {fact_id!r} is not in the pinned image")
            continue
        if after is None:
            problems.append(f"consumed fact {fact_id!r} is not in the working tree")
            continue
        for field in COMPARED_FIELDS:
            if before[field] != after[field]:
                problems.append(
                    f"consumed fact {fact_id!r} changed {field}: "
                    f"{before[field]!r} -> {after[field]!r}"
                )
    return problems


def git_blob(root: Path, commit: str, relative: str) -> tuple[bytes, str | None]:
    """Байты ``relative`` в коммите ``commit``, или строка ошибки."""
    try:
        result = subprocess.run(
            ("git", "cat-file", "blob", f"{commit}:{relative}"),
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return b"", f"git is not available: {exc}"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        return b"", f"git cat-file blob {commit[:12]}:{relative} failed: {detail or result.returncode}"
    return result.stdout, None
