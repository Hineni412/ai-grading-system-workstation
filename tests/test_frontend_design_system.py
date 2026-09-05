from __future__ import annotations

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "frontend" / "src"
TOKENS = SRC / "styles" / "tokens.css"
# Theme token definition files: hex literals there define the design tokens
# themselves (tokens.css, and the shadcn-vue theme variables in tailwind.css),
# they are not page-specific color exceptions.
TOKEN_DEFINITION_FILES = {
    TOKENS,
    SRC / "styles" / "tailwind.css",
}
COLOR_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgb|hsl)a?\(")
DESIGN_PROPERTY = (
    r"(?:margin|padding)(?:-[a-z]+)*"
    r"|(?:row-|column-)?gap"
    r"|border(?:-[a-z]+)*-radius"
    r"|box-shadow"
)
NONZERO_DESIGN_NUMBER = (
    r"(?<![\w.-])-?"
    r"(?:0*\.[0-9]*[1-9][0-9]*|0*[1-9][0-9]*(?:\.[0-9]+)?)"
    r"(?:px|rem|em|%)?"
)
RAW_DESIGN_VALUE = re.compile(
    rf"(?<![\w-])(?:{DESIGN_PROPERTY})\s*:\s*[^;{{}}]*?"
    rf"{NONZERO_DESIGN_NUMBER}",
    re.IGNORECASE,
)


def test_design_tokens_define_the_approved_visual_contract() -> None:
    css = TOKENS.read_text(encoding="utf-8")
    required = {
        "--color-accent: #135e6b",
        "--color-accent-hover: #0e4a54",
        "--space-1: 4px",
        "--space-9: 48px",
        "--radius-control: 8px",
        "--radius-overlay: 14px",
        "--shadow-overlay:",
        "--duration-fast: 100ms",
        "--duration-base: 160ms",
    }
    lowered = css.lower()
    assert all(token in lowered for token in required)


def test_page_specific_color_exceptions_are_confined_to_p3_5_question_work() -> None:
    offenders: list[str] = []
    for path in SRC.rglob("*"):
        if path.suffix not in {".css", ".vue"} or path in TOKEN_DEFINITION_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if COLOR_LITERAL.search(text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert set(offenders) == {
        "frontend/src/styles/question-bank.css",
        "frontend/src/views/ResultsCenterView.vue",
        "frontend/src/views/StudentEvidenceView.vue",
        "frontend/src/components/training/PersonalizedRecommendationDraft.vue",
        "frontend/src/workspaces/class-teacher/affairs/SopFlowDiagram.vue",
        "frontend/src/workspaces/class-teacher/students/AcademicAnalysisPanel.vue",
        "frontend/src/workspaces/class-teacher/students/AcademicOverviewPanel.vue",
        "frontend/src/workspaces/class-teacher/students/EvidenceSessionsPanel.vue",
        "frontend/src/workspaces/class-teacher/students/EvidenceUploadPanel.vue",
        # 班主任原型稿（VariantA/B/C 等设计对比稿）随正式面板一并登记。
        "frontend/src/workspaces/class-teacher/prototype/PrototypeAside.vue",
        "frontend/src/workspaces/class-teacher/prototype/SopWorkspacePrototype.vue",
        "frontend/src/workspaces/class-teacher/prototype/VariantA.vue",
        "frontend/src/workspaces/class-teacher/prototype/VariantB.vue",
        "frontend/src/workspaces/class-teacher/prototype/VariantC.vue",
    }


def test_question_selection_uses_a_border_token_not_a_document_flow_shadow() -> None:
    tokens = TOKENS.read_text(encoding="utf-8")
    queue_css = (SRC / "styles" / "review-queue.css").read_text(encoding="utf-8")

    assert "--border-selected-width: 4px" in tokens
    assert "border-block-end: var(--border-selected-width) solid" in queue_css
    assert "--shadow-selected" not in tokens
    assert "box-shadow: var(--shadow-selected" not in queue_css


@pytest.mark.parametrize(
    "declaration",
    (
        "padding-inline: 13px;",
        "margin: var(--space-2) 19px;",
        "row-gap: 0.5rem;",
        "box-shadow: 0 2px 8px var(--color-border-default);",
    ),
)
def test_design_value_guard_rejects_logical_multi_value_and_shadow_bypasses(
    declaration: str,
) -> None:
    assert RAW_DESIGN_VALUE.search(declaration)


def test_element_plus_is_not_part_of_the_design_system() -> None:
    # The frontend migrated to the shadcn-vue design system
    # (commit "feat(ui): migrate frontend to shadcn-vue design system"):
    # element-theme.css was deleted and element-plus was removed from
    # package.json. Guard against element-plus being reintroduced.
    main = (SRC / "main.ts").read_text(encoding="utf-8")
    app = (SRC / "App.vue").read_text(encoding="utf-8")
    package_json = (ROOT / "frontend" / "package.json").read_text(encoding="utf-8")

    assert "element-plus" not in main
    assert "element-plus" not in app
    assert "ElConfigProvider" not in app
    assert '"element-plus"' not in package_json
    assert not (SRC / "styles" / "element-theme.css").exists()
