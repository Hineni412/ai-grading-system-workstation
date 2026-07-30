from __future__ import annotations

from typing import Any, Protocol


class MaterialReader(Protocol):
    def inspect(self, source_ref: str) -> dict[str, Any]:
        """Return non-sensitive material metadata for a controlled reference."""


class QuestionEvidenceReader(Protocol):
    def list_questions(self) -> list[dict[str, Any]]:
        """Return safe question choices without filesystem references."""

    def read(self, query: dict[str, Any]) -> dict[str, Any]:
        """Return selected, versioned question evidence without writing."""


class AssessmentEvidenceReader(Protocol):
    def list_assessments(self) -> list[dict[str, Any]]:
        """Return safe assessment choices without student identities."""

    def read(self, query: dict[str, Any]) -> dict[str, Any]:
        """Return an anonymous, versioned class-level evidence snapshot."""


class LessonModelAdapter(Protocol):
    def generate(
        self,
        *,
        operation_id: str,
        resource_pack: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate one structured draft without automatic retry."""


class WpsAdapter(Protocol):
    def execute(
        self,
        *,
        operation_id: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute an approved structured plan against an isolated copy."""
