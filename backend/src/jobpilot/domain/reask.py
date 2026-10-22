"""Which of the questions an agent reported are genuinely new, and which the owner has already settled.

The agent keys its questions per run (`page:0:reported:K`), so a re-run reports the same question under a new key. The
only identity that survives is the normalised label, which is also how saved answers are keyed. Pure rules, no IO."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .answers import JobContext, Known, build_plan
from .canonical import norm

NON_ASKABLE = frozenset({"file", "secret"})  # never answered by typing: classified as new so they stay visible


@dataclass(frozen=True)
class Settled:
    """A reported question that already has an answer; `value` is None when the owner filled it in the tab."""

    label: str
    value: str | None
    source: str


@dataclass(frozen=True)
class Classified:
    new: list[int]  # indexes into the reported list that still need the owner
    settled: list[tuple[int, Settled]]


def classify_reported(
    questions: Sequence[Mapping[str, Any]],
    known: Known,
    job_id: str,
    company_slug: str,
    ctx: JobContext,
    completed: Iterable[str] = (),
) -> Classified:
    """Split reported questions into new and already-settled ones.

    Settled means: the owner told the tab-completion action they filled it in (`completed`, normalised labels), or the
    answer bank (job, company, global scope), the profile or a derived value resolves it. One field at a time, so a
    question's own options decide whether a saved value fits it."""
    done = set(completed)
    new: list[int] = []
    settled: list[tuple[int, Settled]] = []
    for index, question in enumerate(questions):
        label = str(question.get("label") or "")
        if question.get("kind") in NON_ASKABLE or not norm(label):
            new.append(index)
            continue
        if norm(label) in done:
            settled.append((index, Settled(label, None, "owner-completed")))
            continue
        field = {
            "key": str(index),
            "label": label,
            "type": question.get("kind") or "text",
            "required": True,
            "options": list(question.get("options") or []),
        }
        plan, _ = build_plan([field], known, job_id, company_slug, ctx)
        if plan:
            settled.append((index, Settled(label, plan[0].value, plan[0].source)))
        else:
            new.append(index)
    return Classified(new, settled)
