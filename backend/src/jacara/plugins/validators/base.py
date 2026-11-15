"""Rule validation extension point: cross-check the numbers on a document against each other and
against common sense.

Evidence alignment can only tell whether a value is in the source text. If OCR misread the source
(1.00 as 100), alignment treats the wrong value as having evidence. Rule validation takes another
angle: cash − total should equal change; if not, some value is wrong, however high the OCR
confidence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from jacara.plugins.extractors.base import ExtractedField

Severity = Literal["error", "warning"]
"""error: some value is definitely wrong. warning: suspicious but possibly correct (for example a
future date may mean a wrong computer clock)."""

Values = dict[str, str | float | None]
"""Field name → value. Rules look only at values, regardless of how they were extracted."""


@dataclass(frozen=True)
class Rule:
    """A rule: name, required fields, check.

        check returns None on success and a sentence on failure (shown to the user as is). If any
    field in needs has no value, the rule is skipped and does not count as failed.

        Two kinds of table (line item) rules:
        - needs contains items.*.amount: "the amounts of all rows"; checked when at least one row
          has a value.
        - per_row="items": checked once per row; needs lists column names (quantity, amount) and
          check receives that row's values.
        flags: fields highlighted on failure. Defaults to all of needs."""

    id: str
    title: str
    needs: tuple[str, ...]
    check: Callable[[Values], str | None]
    severity: Severity = "error"
    per_row: str | None = None
    flags: tuple[str, ...] | None = None
    requires: tuple[str, ...] = ()
    """The rule applies only when the template contains these fields (values may be empty). Rules
    that check for missing information rely on this and appear only in relevant templates (cash
    register TSE rules do not appear in German invoice templates)."""


@dataclass(frozen=True)
class Issue:
    """A failed rule. fields are the fields involved: it is unknown which one is wrong, so all are
    flagged."""

    rule: str
    title: str
    message: str
    fields: tuple[str, ...]
    severity: Severity


@dataclass(frozen=True)
class ValidationResult:
    """Validation result: failed, passed, and skipped for missing fields."""

    issues: tuple[Issue, ...]
    passed: tuple[str, ...]
    skipped: tuple[str, ...]

    def fields_with_issues(self) -> set[str]:
        """Names of all fields involved in issues."""
        return {name for issue in self.issues for name in issue.fields}


class Validator(Protocol):
    """Validator interface: any class with a name attribute and a validate method is a validator."""

    name: str

    def validate(self, fields: Sequence[ExtractedField]) -> ValidationResult:
        """Check a set of fields and return the issues."""
        ...
