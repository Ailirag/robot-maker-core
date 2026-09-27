"""Turn geometry findings into explicit model work, without granting physical approval."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path

from engineering.build_composition import _path
from engineering.delivery import read_bound

WORKFLOW = (
    "Открой указанную деталь и область: координаты относятся к исходному STEP, а не обязательно к сборке.",
    "Установи назначение участка, путь нагрузки, способ печати и сопряжения. Срабатывание ещё не доказывает дефект.",
    "В задаче исправления продолжай доступные доработки без вопроса «Продолжать?». Подтверждённый дефект исправляй в авторском коде новой ревизии; отчёт и список задач не заменяют исправление.",
    "Сначала воспроизведи недостаток проверкой, затем измени геометрию и повтори поиск. Перепроверь посадки, контур, коллизии, сборочный доступ, печать и инструкцию.",
    "Каждое оставшееся срабатывание разбери отдельно. Намеренную особенность или погрешность поиска можно принять только с основанием, рассмотренным усилением и способом проверки.",
    "Если нужны неизвестные размеры или нагрузка, сохрани null с obtained_by и точный вопрос; продолжай независимые исправления. Заполнение не заменяет материал в геометрии.",
    "Исторический отчёт указывает, что проверять, но не разрешает новую выдачу. Нужна свежая проверка текущей геометрии; физические допуски определяют прежние авторитеты.",
)
CHECKS = ("regression_on_original_geometry", "new_geometry_rescan",
          "interfaces_clearances_envelope_collisions", "assembly_access",
          "print_orientation_supports_and_sliced_project", "regenerated_instruction")
SUGGESTIONS = {
    "THIN_WALL": ("Увеличить материал вокруг отверстия или прорези либо изменить их глубину/расположение с сохранением функции.",
                  "Различить несущую стенку, дно глухого отверстия, гибкую губу и клиновидный остаток у кромки."),
    "NARROW_CONNECTION": ("Расширить корень, продлить полноценный стык; рассмотреть ребро или плавный переход.",
                          "Проверить путь нагрузки: исчезновение стенки при воксельной эрозии само по себе не доказывает слабость."),
    "PARTIAL_CONTACT": ("Обеспечить полное опирание и достаточную глубину соединения, исключить касание только краем.",
                        "Пересчитать контакт по родительской и присоединяемой геометрии в общем датуме."),
    "SMALL_SECTION": ("Найти участок малого сечения в STEP и сопоставить его с направлением нагрузки.",
                      "Проверить, не является ли срез касательным краем отверстия; при реальной перемычке увеличить сечение."),
}
PENDING = {"needs_repair": ("repair_geometry", {"confirmed_defect", "potential_defect"}),
           "needs_investigation": ("investigate_geometry", {"potential_defect", "sampling_uncertainty"}),
           "needs_measurement": ("obtain_missing_evidence", {"missing_evidence"})}
METHOD_SCHEMAS = {"sections": "structural-section-scan-v1",
                  "walls": "surface-thickness-scan-v1",
                  "connections": "connection-neck-scan-v1"}


def identity():
    return dict(feedback_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def feedback(findings, decisions=None):
    """Decisions can request work; only justified non-defect classifications accept."""
    decisions = {} if decisions is None else decisions
    if not isinstance(findings, list) or any(not isinstance(f, dict) or
            not _text(f.get("id")) or not _text(f.get("kind")) for f in findings):
        raise ValueError("geometry feedback: malformed finding")
    ids = [f["id"] for f in findings]
    if len(ids) != len(set(ids)):
        raise ValueError("geometry feedback: duplicate finding")
    if not isinstance(decisions, dict) or not set(decisions) <= set(ids):
        raise ValueError("structure: stale or extra finding decision")
    accepted = set()
    for key, row in decisions.items():
        if not isinstance(row, dict):
            raise ValueError("geometry feedback: decision must be an object")
        disposition, classification = row.get("disposition"), row.get("classification")
        if not _text(disposition) or not _text(classification):
            raise ValueError("geometry feedback: disposition and classification required")
        if disposition == "accepted_for_geometry_screen":
            if classification not in {"intentional_feature", "sampling_artifact"}:
                raise ValueError("geometry feedback: classification cannot accept a confirmed defect or missing evidence")
            for field in ("reviewer", "reason", "limitation", "remedy_considered", "verification_basis"):
                if not _text(row.get(field)):
                    raise ValueError("geometry feedback: individual " + field + " required")
            accepted.add(key)
        elif disposition in PENDING:
            if classification not in PENDING[disposition][1]:
                raise ValueError("geometry feedback: incompatible pending classification")
            for field in ("reviewer", "reason", "planned_change"):
                if not _text(row.get(field)):
                    raise ValueError("geometry feedback: pending " + field + " required")
        else:
            raise ValueError("geometry feedback: unsupported disposition; a stored fixed/PASS is not a rescan")
    cards = []
    for finding in findings:
        row = decisions.get(finding["id"], {})
        disposition = row.get("disposition")
        action = ("accepted_with_limits" if finding["id"] in accepted else
                  PENDING[disposition][0] if disposition in PENDING else "inspect_then_repair")
        cards.append(dict(id=finding["id"], kind=finding["kind"], action=action,
            finding=copy.deepcopy(finding), decision=copy.deepcopy(row),
            suggested_changes=list(SUGGESTIONS.get(finding["kind"],
                ("Определить причину срабатывания; неизвестный тип нельзя автоматически принять.",))),
            required_checks=list(CHECKS)))
    cards.sort(key=lambda c: (0 if c["action"] == "repair_geometry" else 1, c["id"]))
    unresolved = sorted(set(ids)-accepted)
    return dict(schema="geometry-model-feedback-v1",
                status="REVIEW_REQUIRED" if unresolved else "REVIEWED",
                unresolved=unresolved, cards=cards, workflow=list(WORKFLOW),
                physical_approval=False, strength_n=None,
                obtained_by="Qualified load/material evidence and physical tests remain separate.")


def _record(root, name, row):
    if not isinstance(row, dict) or not _text(row.get("interpretation")):
        raise ValueError("geometry feedback: part interpretation required")
    data = json.loads(read_bound(root, row["receipt"]))
    if not isinstance(data, dict):
        raise ValueError("geometry feedback: receipt must be an object")
    inputs = data.get("input", {})
    if not isinstance(inputs, dict) or not isinstance(inputs.get("job", {}), dict):
        raise ValueError("geometry feedback: malformed part input")
    if inputs.get("part") != name:
        raise ValueError("geometry feedback: wrong part receipt")
    source = inputs.get("step", inputs.get("job", {}).get("step"))
    read_bound(root, source)
    methods = data.get("methods", {})
    if not isinstance(methods, dict) or set(methods)-set(METHOD_SCHEMAS):
        raise ValueError("geometry feedback: unsupported method set")
    coverage = data.get("coverage", {})
    if not isinstance(coverage, dict):
        raise ValueError("geometry feedback: malformed coverage")
    complete = coverage.get("complete")
    if type(complete) is not bool:
        raise ValueError("geometry feedback: explicit coverage required")
    findings, errors = [], {}
    for method, schema in METHOD_SCHEMAS.items():
        item = methods.get(method, {})
        if not isinstance(item, dict):
            raise ValueError("geometry feedback: malformed method")
        report = item.get("report")
        if item.get("complete") is not True:
            errors[method] = item.get("error", "Incomplete coverage; rerun or refine the method")
        if report is not None:
            if (not isinstance(report, dict) or report.get("schema") != schema
                    or not isinstance(report.get("findings"), list)):
                raise ValueError("geometry feedback: unsupported method report")
            report_coverage = report.get("coverage", {})
            if not isinstance(report_coverage, dict):
                raise ValueError("geometry feedback: malformed method coverage")
            method_complete = report_coverage.get("complete") if method != "sections" else True
            if item.get("complete") is True and method_complete is not True:
                raise ValueError("geometry feedback: false complete method")
            findings.extend(report["findings"])
        elif item.get("complete") is True:
            raise ValueError("geometry feedback: completed method has no report")
    geometry = data.get("geometry", {})
    if not isinstance(geometry, dict):
        raise ValueError("geometry feedback: malformed geometry result")
    if geometry.get("valid") is not True or geometry.get("solids") != 1:
        errors["geometry"] = data.get("error", "Valid single-solid geometry required")
    if complete != (not errors):
        raise ValueError("geometry feedback: inconsistent complete receipt")
    model = feedback(findings)
    return dict(receipt=row["receipt"], geometry=source,
                coordinate_system=inputs.get("coordinate_system", "Source STEP coordinates"),
                recorded_identity=data.get("identity"), interpretation=row["interpretation"],
                coverage_complete=complete, scan_errors=errors,
                finding_count=len(findings), kind_counts=dict(Counter(f["kind"] for f in findings)),
                next_action="repair_scan_then_rerun" if errors else
                            "inspect_then_repair" if findings else "proceed_to_other_checks",
                cards=model["cards"])


def load_feedback(root, index_path, *, part=None, compact=False):
    """Relay a hash-bound baseline for repair; never promote it to fresh verification."""
    root = Path(root).resolve()
    index_path = str(index_path)
    raw = _path(root, index_path).read_bytes()
    index = json.loads(raw)
    if not isinstance(index, dict) or index.get("schema") != "geometry-feedback-index-v1":
        raise ValueError("geometry feedback: unsupported index")
    expected, entries = index.get("expected_parts"), index.get("parts")
    if (not isinstance(expected, list) or not expected or not all(_text(n) for n in expected)
            or len(expected) != len(set(expected)) or not isinstance(entries, dict)
            or set(expected) != set(entries)):
        raise ValueError("geometry feedback: missing, extra or duplicate part inventory")
    rows = {name: _record(root, name, entries[name]) for name in expected}
    if part is not None and part not in rows:
        raise ValueError("geometry feedback: unknown requested part")
    status = ("COVERAGE_INCOMPLETE" if any(r["scan_errors"] for r in rows.values()) else
              "ACTION_REQUIRED" if any(r["finding_count"] for r in rows.values()) else "SCREENED")
    total = sum(r["finding_count"] for r in rows.values())
    selected = rows if part is None else {part: rows[part]}
    for name, row in selected.items():
        row["detail_argv"] = ["python", "-m", "engineering.geometry_feedback",
                              "--input", index_path, "--part", name]
        if compact:
            row.pop("cards")
    return dict(schema="geometry-feedback-packet-v1", status=status,
                index=dict(path=index_path, sha256=hashlib.sha256(raw).hexdigest()),
                expected_parts=expected, selected_parts=list(selected), parts=selected,
                finding_count=sum(r["finding_count"] for r in selected.values()),
                total_finding_count=total, details_omitted=bool(compact),
                other_parts_omitted=part is not None, workflow=list(WORKFLOW),
                baseline_only=True, requires_fresh_verification=True,
                physical_approval=False, strength_n=None,
                obtained_by="Recorded geometry findings locate work; current verification and physical authorities remain required.")


def for_task(root, refs):
    reports, blockers = [], []
    for ref in refs:
        if not ref.startswith("geometry:"):
            continue
        path = ref.split(":", 1)[1]
        try:
            reports.append(load_feedback(root, path, compact=True))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            message = "geometry feedback " + path + ": " + str(exc)
            reports.append(dict(status="INPUTS_INVALID", index=path, error=message,
                                physical_approval=False, requires_fresh_verification=True))
            blockers.append(message)
    return reports, blockers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--input", required=True)
    parser.add_argument("--part")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = load_feedback(args.root, args.input, part=args.part, compact=args.compact)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(status="INPUTS_INVALID", error=str(exc)), ensure_ascii=False))
        return 4
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "SCREENED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
