"""Assembly steps as data, so a picture can be rendered for every step.

A step names the bodies it adds to the machine, by the names the design's
assembly uses (``assembly()`` rows in machine coordinates). The renderer
draws everything added so far in grey and the new bodies in colour. A step
may be ``blocked_by`` a task when an assemblability record proves it cannot
be performed; the guide then shows the record instead of pretending.

Prose stays prose: ``text`` is what the child reads. The schema only adds
what a picture needs, what a manual lists beside the picture (fasteners,
tools) and what a test can check.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    number: int
    key: str
    title: str
    text: str
    adds: tuple[str, ...] = ()
    view: str = "iso"
    blocked_by: str | None = None
    note: str = ""
    #: what fixes the bodies of this step ("4 × M3×20 сверху во вставки") and
    #: what the hands need ("шестигранник 2,5"): the manual lists them per step
    fasteners: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()
    focus_view: str = "iso"
    focus_transparent: tuple[str, ...] = ()

    @property
    def focus_image_name(self) -> str:
        return f"detail_{self.number:02d}_{self.key}.png"

    @property
    def image_name(self) -> str:
        return f"step_{self.number:02d}_{self.key}.png"


@dataclass(frozen=True)
class StepPlan:
    design_id: str
    base: tuple[str, ...]
    steps: tuple[Step, ...]
    transparent: tuple[str, ...] = field(default_factory=tuple)

    def bodies_through(self, number: int) -> tuple[str, ...]:
        names = list(self.base)
        for step in self.steps:
            if step.number <= number:
                names.extend(step.adds)
        return tuple(names)


def validate(plan: StepPlan, known_bodies: set[str] | None = None) -> list[str]:
    """Problems as text; an empty list means the plan is consistent."""
    problems: list[str] = []
    numbers = [s.number for s in plan.steps]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(f"step numbers must be 1..n, got {numbers}")
    keys = [s.key for s in plan.steps]
    if len(set(keys)) != len(keys):
        problems.append("step keys must be unique")
    seen: dict[str, int] = {}
    for name in plan.base:
        seen[name] = 0
    for step in plan.steps:
        if not step.title.strip() or not step.text.strip():
            problems.append(f"step {step.number}: title and text are required")
        for name in step.adds:
            if name in seen:
                problems.append(f"step {step.number}: {name} was already placed at step {seen[name]}")
            seen[name] = step.number
        if len(step.focus) != len(set(step.focus)) or set(step.focus) - set(seen):
            problems.append(f"step {step.number}: focus must name distinct bodies present at this step")
        if set(step.focus_transparent) - set(step.focus):
            problems.append(f"step {step.number}: transparent focus bodies must belong to focus")
        if step.focus_view not in ('iso', 'rear_iso', 'front', 'left', 'top', 'bottom', 'under_iso'):
            problems.append(f"step {step.number}: focus view is unknown")
    if known_bodies is not None:
        unknown = sorted(set(seen) - set(known_bodies))
        if unknown:
            problems.append(f"bodies not in the design assembly: {unknown}")
    return problems
