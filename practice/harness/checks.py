"""Assertions — expressed as data, so the report can explain exactly what failed.

Each check carries an id, a description, the expected value and the actual value. That
shape is deliberate: a boolean alone tells you nothing at 2am.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Check:
    id: str
    description: str
    expected: Any
    actual: Any
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass
class CheckList:
    checks: list[Check] = field(default_factory=list)

    def add(self, check_id: str, description: str, expected: Any, actual: Any) -> Check:
        passed = expected == actual
        check = Check(check_id, description, expected, actual, passed)
        self.checks.append(check)
        return check

    def true(self, check_id: str, description: str, condition: bool, detail: str = "") -> Check:
        check = Check(check_id, description, True, bool(condition), bool(condition), detail)
        self.checks.append(check)
        return check

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def passed(self) -> bool:
        return not self.failed

    def as_list(self) -> list[dict]:
        return [c.as_dict() for c in self.checks]
