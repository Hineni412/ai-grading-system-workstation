from __future__ import annotations

import json
from pathlib import Path
from dataclasses import replace
from typing import Any, Mapping

import httpx
import openai
import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.training_criteria import (
    AnalysisConflictError,
    CombinedAnalysisRepository,
    CombinedQuestionAnalysisModule,
    ExistingTagProjectionWriter,
    GatewayBatchResponse,
    GatewayUsage,
    QuestionAnalysisImage,
    QuestionAnalysisInput,
    QuestionAnalysisWorkItem,
)

from tests.current_knowledge_support import install_current_knowledge


FIXTURE = Path(__file__).parent / "fixtures" / "p4_00_gold_set.json"


def _type_question(*, revision: int = 11) -> QuestionAnalysisInput:
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision

    release = load_release_for_taxonomy_revision(revision)
    contract = dict(_question(1).taxonomy_contract)
    contract.update(
        taxonomy_revision=revision, knowledge_catalog_revision=revision,
        knowledge_graph_release_id=release.release_id,
        curriculum_volume={"id": "bnu24-math-g8-upper", "sections": [
            {"id": "kp_bnu24_math_g8_upper_1_1", "name": "探索勾股定理"},
        ]},
    )
    return replace(_question(1), taxonomy_contract=contract, tagging_context=replace(
        _question(1).tagging_context, question_type="解答题", curriculum_volume_id="bnu24-math-g8-upper",
    ))


def _type_labels(primary: str = "kp_bnu24_math_g8_upper_1_1_t03") -> dict[str, Any]:
    return {"primary_type_id": primary, "secondary_type_ids": ["kp_bnu24_math_g8_upper_1_1_t01"],
            "proposed_type_name": "", "reason": "按整题核心任务判定。"}


def _type_result() -> dict[str, Any]:
    from question_bank.training_criteria.adapters import _combined_evidence_examples

    part = _combined_evidence_examples()["q11_process_positive"]
    part.pop("why_correct")
    return {
        "question_id": 1, "tag_analysis": _tag_payload(), "question_type_labels": _type_labels(),
        "solution_evidence": {"schema_version": "question-solution-evidence-v2", "question_id": 1,
            "parts": [part], "auxiliary_rules": [], "rationale": "合成解题依据。", "confidence": 0.9},
    }


def test_v9_import_candidates_are_scoped_and_prompt_preserves_type_definitions() -> None:
    from question_bank.training_criteria.adapters import _combined_prompt
    from question_bank.training_criteria.analysis import (
        PlannedAnalysisBatch, combined_response_format, controlled_term_ids_from_questions,
    )
    from question_bank.question_types import is_type_key

    question = _type_question()
    types = [item for item in question.taxonomy_contract["candidates"]["knowledge"] if is_type_key(item["id"])]
    assert len(types) == 6
    assert all(item["parent_id"] == "kp_bnu24_math_g8_upper_1_1" for item in types)
    sent = json.loads(_combined_prompt(PlannedAnalysisBatch((question,), 1000, 1000), "both")[1]["content"][0]["text"])
    assert sent["questions"][0]["candidate_contract"]["question_type_mode"] is True
    assert types[0] in sent["questions"][0]["candidate_contract"]["candidates"]["knowledge"]
    assert "最多 2 个" in sent["rules"]
    assert sent["prompt_version"] == "combined-v4-question-types"
    schema = combined_response_format(allowed_term_ids=controlled_term_ids_from_questions((question,)))
    assert schema["name"] == "question_bank_combined_analysis_v4"
    labels = schema["schema"]["properties"]["results"]["items"]["properties"]["question_type_labels"]
    assert labels["properties"]["secondary_type_ids"]["maxItems"] == 2
    assert "kp_bnu24_math_g8_upper_1_2_t06" not in labels["properties"]["primary_type_id"]["enum"]
    with pytest.raises(ValueError, match="another question"):
        replace(question, question_id=2)


def test_v8_import_prompt_and_source_hash_remain_compatible() -> None:
    from question_bank.training_criteria.analysis import combined_response_format, controlled_term_ids_from_questions, normalize_question_type_result, solution_evidence_source_content_hash

    old = _type_question(revision=10)
    current = replace(old, taxonomy_contract=_type_question().taxonomy_contract)
    assert not old.taxonomy_contract.get("question_type_mode")
    assert current.source_content_hash == old.source_content_hash
    assert solution_evidence_source_content_hash(current) == solution_evidence_source_content_hash(old)
    assert combined_response_format(allowed_term_ids=controlled_term_ids_from_questions((old,)))["name"].endswith("_v3")
    raw = {"question_id": 1, "solution_evidence": {"parts": []}}
    assert normalize_question_type_result(old, raw) == raw


@pytest.fixture(params=[False, True])
def source_content_reuse(request):
    from question_bank.training_criteria.analysis import source_content_hash_reuse

    if request.param:
        with source_content_hash_reuse():
            yield
    else:
        yield


def test_question_content_identity_is_shared_and_excludes_storage_and_layout(source_content_reuse) -> None:
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    from question_bank.services.standard_difficulty import question_content_fingerprint

    image = QuestionAnalysisImage(role="question", mime_type="image/png", content=b"TEST-image")
    original = replace(_question(1, has_images=True, images=(image,)),
        tagging_context=replace(_question(1).tagging_context,
            question_text="给定 $x \\text{a b}$ [[IMAGE:old/image.png]]", has_images=True),
        rich_question_blocks=({"text": "补充作答要求", "style": "old", "version": 2},),
        reference_solution={"text": "用等式性质求解", "source_segments": [], "rich_blocks": [],
                            "trust_level": "source_extracted", "source_kind": "answer"})
    moved = replace(original, question_id=2, taxonomy_contract={},
        question_type_confirmed=True,
        tagging_context=replace(original.tagging_context,
            question_text="给定 $x \\text{a b}$ [[IMAGE:new/image.png]]", question_number="99",
            curriculum_volume_id="TEST-other-volume", grade="TEST-grade",
            existing_tags=["TEST-tag"], evidence_parts=[{"part_id": "TEST-derived"}]),
        rich_question_blocks=({"text": "补充作答要求", "style": "new", "version": 3,
                               "image_relationships": {"rId1": "new/image.png"}},))
    identity = original.source_content_hash
    assert moved.source_content_hash == identity
    assert original.criterion_source_content_hash == identity
    assert solution_evidence_source_content_hash(original) == identity
    assert question_content_fingerprint(original) == identity
    assert replace(original, reference_solution={**original.reference_solution,
        "trust_level": "teacher_confirmed", "source_kind": "teacher"}).source_content_hash == identity
    second_image = QuestionAnalysisImage(role="question", mime_type="image/png", content=b"TEST-second")
    ordered = replace(original, images=(image, second_image))
    assert replace(ordered, images=(second_image, image)).source_content_hash != ordered.source_content_hash
    for changed in (
        replace(original, tagging_context=replace(original.tagging_context, answer_text="x=2")),
        replace(original, tagging_context=replace(original.tagging_context, question_type="填空题")),
        replace(original, tagging_context=replace(original.tagging_context,
            question_text="给定 $x \\text{ab}$ [[IMAGE:old/image.png]]")),
        replace(original, images=(QuestionAnalysisImage(role="question", mime_type="image/png",
                                                        content=b"TEST-changed-image"),)),
        replace(original, rich_question_blocks=({"text": "补充不同作答要求"},)),
        replace(original, reference_solution={**original.reference_solution, "text": "改用不同依据"}),
    ):
        assert changed.source_content_hash != identity


@pytest.fixture
def word_content_cache(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[str]]:
    from integration.result_cache import ResultCache
    from question_bank.training_criteria import analysis

    monkeypatch.setattr(analysis, "_WORD_CONTENT_CACHE", ResultCache(128, max_bytes=8 * 1024 * 1024))
    original = analysis.ET.fromstring
    parsed: list[str] = []

    def parse(xml: str) -> Any:
        parsed.append(xml)
        return original(xml)

    monkeypatch.setattr(analysis.ET, "fromstring", parse)
    return analysis, parsed


def test_word_content_cache_reuses_parsing_and_keeps_nested_values_private(word_content_cache) -> None:
    analysis, parsed = word_content_cache
    xml = ('<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>'
           '<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    expected = [{"table": [["A", "B"]]}, {"word_text": "AB"}]
    first = analysis._word_content(xml, "")
    assert first == expected
    first[0]["table"][0][0] = "TEST-mutated-owner"
    second = analysis._word_content(xml, "")
    assert second == expected
    second[0]["table"][0].clear()
    assert analysis._word_content(xml, "") == expected
    assert parsed == [xml]


def test_word_content_cache_tracks_xml_and_plain_text_including_mutable_input_blocks(word_content_cache) -> None:
    analysis, parsed = word_content_cache
    xml = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:r><w:t>A</w:t></w:r></w:p>')
    changed_xml = xml.replace("<w:t>A</w:t>", "<w:t>C</w:t>")
    assert analysis._word_content(xml, "A") == []
    assert analysis._word_content(xml, "A") == []
    assert analysis._word_content(xml, "B") == [{"word_text": "A"}]
    assert analysis._word_content(changed_xml, "A") == [{"word_text": "C"}]
    question = replace(_question(1), word_question_blocks=({"text": "A", "xml": xml},))
    original_hash = question.source_content_hash
    block = question.word_question_blocks[0]
    assert isinstance(block, dict)
    block["xml"] = changed_xml
    assert question.source_content_hash != original_hash
    assert parsed == [xml, xml, changed_xml]


def test_word_content_cache_does_not_cache_damaged_xml(word_content_cache) -> None:
    analysis, parsed = word_content_cache
    damaged = "<TEST-unclosed>"
    for _ in range(2):
        with pytest.raises(ValueError, match="题目 Word 内容损坏，不能核对公式内容"):
            analysis._word_content(damaged, "")
    assert parsed == [damaged, damaged]


@pytest.mark.parametrize("oversized_field", ["xml", "plain_text"])
def test_word_content_cache_parses_large_inputs_without_retaining_them(word_content_cache, oversized_field) -> None:
    analysis, parsed = word_content_cache
    xml = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:r><w:t>A</w:t></w:r></w:p>')
    plain_text = "A"
    if oversized_field == "xml":
        xml += " " * (64 * 1024)
    else:
        plain_text = "A" * (64 * 1024)
    expected = [] if oversized_field == "xml" else [{"word_text": "A"}]
    assert analysis._word_content(xml, plain_text) == expected
    assert analysis._word_content(xml, plain_text) == expected
    assert parsed == [xml, xml]


def test_content_identity_preserves_word_formulas_and_table_semantics(monkeypatch: pytest.MonkeyPatch, source_content_reuse) -> None:
    from question_bank.training_criteria import analysis

    def assert_uncached_hash(question: QuestionAnalysisInput) -> None:
        with monkeypatch.context() as uncached:
            uncached.setattr(analysis, "_word_content", analysis._parse_word_content)
            expected = analysis._compute_question_content_hash(question)
        assert question.source_content_hash == expected

    xml = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">'
           '<w:pPr><w:jc w:val="left"/></w:pPr><m:oMath><m:r><w:rPr><w:b/></w:rPr>'
           '<m:t>x + 1</m:t></m:r></m:oMath></w:p>')
    original = replace(_question(1), word_question_blocks=({"text": "相同提取文字", "xml": xml},))
    layout = replace(original, word_question_blocks=({"text": "相同提取文字",
        "xml": xml.replace('w:val="left"', 'w:val="center"').replace("<w:b/>", "<w:i/>"),},))
    changed = replace(original, word_question_blocks=({"text": "相同提取文字",
        "xml": xml.replace("x + 1", "x + 2"),},))
    assert layout.source_content_hash == original.source_content_hash
    assert changed.source_content_hash != original.source_content_hash
    for question in (original, layout, changed):
        assert_uncached_hash(question)
    native = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:r><w:t>原文字</w:t></w:r></w:p>')
    original = replace(original, word_question_blocks=({"text": "原文字", "xml": native},))
    changed = replace(original, word_question_blocks=({"text": "原文字",
        "xml": native.replace("原文字", "修改后文字"),},))
    assert changed.source_content_hash != original.source_content_hash
    for question in (original, changed):
        assert_uncached_hash(question)
    table = ('<w:tbl xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
             '<w:tr><w:tc><w:p><w:r><w:t>A</w:t></w:r></w:p></w:tc>'
             '<w:tc><w:p><w:r><w:t>B</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    original = replace(original, word_question_blocks=({"text": "相同提取文字", "xml": table},))
    changed = replace(original, word_question_blocks=({"text": "相同提取文字",
        "xml": table.replace("<w:t>A</w:t>", "<w:t>C</w:t>"),},))
    assert changed.source_content_hash != original.source_content_hash
    for question in (original, changed):
        assert_uncached_hash(question)
    positioned = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                  '<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r></w:p>')
    original = replace(original, word_question_blocks=({"text": "2", "xml": positioned},))
    changed = replace(original, word_question_blocks=({"text": "2",
        "xml": positioned.replace("superscript", "subscript")},))
    assert changed.source_content_hash != original.source_content_hash
    for question in (original, changed):
        assert_uncached_hash(question)


def test_supported_stored_source_hashes_are_accepted_only_for_unchanged_content(source_content_reuse) -> None:
    from question_bank.training_criteria.analysis import source_content_hash_matches

    question = _question(1)
    stored = {
        "tag": ("076ad0923f0cece31309d7eea41d35f876e2e67b2ae429875ea0e2d5550bd365",
                "8448295e74be2c25cf76efddcf9c7af995f128a6657daa6a12cff49596e6bfee"),
        "training_criteria": ("c5bd16ebc5eb137f2de671d3555677a4588f830b841922c14d99099015a09f56",),
        "solution_evidence": ("e5f583c371a720969f6e0d5544c77742cca4cbade66910264667556c31eaefd6",),
    }
    changed = replace(question, tagging_context=replace(question.tagging_context, answer_text="x=2"))
    for kind, hashes in stored.items():
        assert source_content_hash_matches(question, question.source_content_hash, kind=kind)
        for fingerprint in hashes:
            assert source_content_hash_matches(question, fingerprint, kind=kind)
            assert source_content_hash_matches(
                replace(question, question_type_confirmed=not question.question_type_confirmed),
                fingerprint, kind=kind,
            )
            assert not source_content_hash_matches(changed, fingerprint, kind=kind)


def test_source_content_reuse_is_compact_bounded_and_released(monkeypatch: pytest.MonkeyPatch) -> None:
    from question_bank.training_criteria import analysis

    original = analysis._compute_question_content_hash
    computed: list[int] = []

    def compute(question):
        computed.append(question.question_id)
        return original(question)

    monkeypatch.setattr(analysis, "_compute_question_content_hash", compute)
    monkeypatch.setattr(analysis, "_SOURCE_CONTENT_REUSE_LIMIT", 2)
    question = replace(_question(1), rich_question_blocks=({"text": "TEST-" + "A" * 100_000},))
    with analysis.source_content_hash_reuse():
        reuse = analysis._SOURCE_CONTENT_REUSE.get()
        assert question.source_content_hash == replace(question, question_id=2, taxonomy_contract={}).source_content_hash
        assert computed == [1]
        assert all(type(key) is bytes and len(key) == 32 for key in reuse.values)
        assert all(type(value) is str and len(value) == 64 for value in reuse.values.values())
        with analysis.source_content_hash_reuse():
            assert analysis._SOURCE_CONTENT_REUSE.get() is reuse
            assert question.source_content_hash == original(question)
        for text in ("TEST-other-A", "TEST-other-B"):
            replace(question, tagging_context=replace(question.tagging_context, question_text=text)).source_content_hash
        assert len(reuse.values) == 2
        question.source_content_hash
        assert computed == [1, 1, 1, 1]
    assert not reuse.values
    assert analysis._SOURCE_CONTENT_REUSE.get() is None
    with analysis.source_content_hash_reuse():
        question.source_content_hash
    assert computed == [1, 1, 1, 1, 1]


@pytest.mark.parametrize("field", ["word", "rich", "reference", "tags", "repair", "taxonomy"])
def test_compatible_source_reuse_checks_complete_mutable_content(monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    from question_bank.training_criteria import analysis

    xml = '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:r><w:t>A</w:t></w:r></w:p>'
    question = replace(_question(1),
        word_question_blocks=({"text": "A", "xml": xml},),
        rich_question_blocks=({"text": "A", "nested": {"values": ["TEST-original"]}},),
        reference_solution={"text": "TEST-reference", "source_segments": ["TEST-segment"],
            "rich_blocks": [], "trust_level": "source_extracted", "source_kind": "answer"},
        repair_context={"mode": "repair_previous_rejected_result", "validation_error": "TEST-error",
            "previous_result": {"nested": ["TEST-original"]}})
    original = analysis._compute_compatible_source_content_hashes
    computed: list[str] = []

    def compute(question, *, kind):
        computed.append(kind)
        return original(question, kind=kind)

    monkeypatch.setattr(analysis, "_compute_compatible_source_content_hashes", compute)
    with analysis.source_content_hash_reuse():
        first = analysis.compatible_source_content_hashes(question, kind="training_criteria")
        assert analysis.compatible_source_content_hashes(question, kind="training_criteria") is first
        assert computed == ["training_criteria"]
        if field == "word":
            question.word_question_blocks[0]["xml"] = xml.replace(">A<", ">B<")
        elif field == "rich":
            question.rich_question_blocks[0]["nested"]["values"].append("TEST-change")
        elif field == "reference":
            question.reference_solution["source_segments"].append("TEST-change")
        elif field == "tags":
            question.tagging_context.existing_tags_by_dimension["special_type"] = ["证明"]
        elif field == "repair":
            question.repair_context["previous_result"]["nested"].append("TEST-change")
        else:
            question = replace(question, taxonomy_contract={"taxonomy_revision": 999})
        changed = analysis.compatible_source_content_hashes(question, kind="training_criteria")
        assert changed == original(question, kind="training_criteria")
        assert computed == ["training_criteria", "training_criteria"]


def test_source_content_reuse_does_not_cache_failures_or_unknown_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from question_bank.training_criteria import analysis

    question = replace(_question(1), word_question_blocks=({"xml": "<TEST-unclosed>"},))
    with analysis.source_content_hash_reuse():
        for _ in range(2):
            with pytest.raises(ValueError, match="题目 Word 内容损坏"):
                question.source_content_hash
        assert not analysis._SOURCE_CONTENT_REUSE.get().values
        question.word_question_blocks[0]["xml"] = ""
        assert question.source_content_hash == analysis._compute_question_content_hash(question)
        with pytest.raises(ValueError, match="source content hash kind is invalid"):
            analysis.compatible_source_content_hashes(question, kind="TEST-invalid")
    class ChangingText:
        value = "TEST-A"
        def __str__(self):
            return self.value
    value = ChangingText()
    question.rich_question_blocks[0]["extra"] = value
    with analysis.source_content_hash_reuse():
        first = analysis.compatible_source_content_hashes(question, kind="training_criteria")
        value.value = "TEST-B"
        second = analysis.compatible_source_content_hashes(question, kind="training_criteria")
        assert first != second
        assert not analysis._SOURCE_CONTENT_REUSE.get().values or all(
            type(entry) is str for entry in analysis._SOURCE_CONTENT_REUSE.get().values.values())


def test_source_reuse_keeps_copied_thread_contexts_private() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    from question_bank.training_criteria import analysis

    question = _question(1)
    with analysis.source_content_hash_reuse():
        expected = question.source_content_hash
        parent_values = analysis._SOURCE_CONTENT_REUSE.get().values
        context = copy_context()
        def read():
            values = analysis._SOURCE_CONTENT_REUSE.get().values
            assert not values
            assert question.source_content_hash == expected
            return values
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker_values = pool.submit(context.run, read).result()
        assert worker_values is not parent_values
        assert len(parent_values) == len(worker_values) == 1
    assert not parent_values
    assert analysis._SOURCE_CONTENT_REUSE.get() is None


def test_source_hash_bundle_reuses_frozen_values_and_preserves_legacy_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    from question_bank.training_criteria import analysis
    from question_bank.solution_evidence import part_assessments

    question = _question(1, question_type="解答题")
    original = part_assessments._compute_input_source_hashes
    computed: list[str] = []
    def compute(question, input_key=""):
        computed.append(input_key)
        return original(question, input_key)
    monkeypatch.setattr(part_assessments, "_compute_input_source_hashes", compute)
    expected = original(question, "TEST-row-A")
    with analysis.source_content_hash_reuse():
        first = part_assessments._input_source_hashes(question, "TEST-row-A")
        assert first == expected
        assert part_assessments._input_source_hashes(replace(question), "TEST-row-A") is first
        assert computed == ["TEST-row-A"]
        second = part_assessments._input_source_hashes(question, "TEST-row-B")
        assert second == original(question, "TEST-row-B")
        assert computed == ["TEST-row-A", "TEST-row-B"]
        for kind, fingerprint in first.legacy:
            assert part_assessments.alias_from_hashes(first, fingerprint) == kind
        changed = replace(question, tagging_context=replace(question.tagging_context, answer_text="TEST-new-answer"))
        changed_hashes = part_assessments._input_source_hashes(changed, "TEST-row-A")
        assert changed_hashes == original(changed, "TEST-row-A")
        assert part_assessments.alias_from_hashes(changed_hashes, first.current) is None


@pytest.mark.parametrize("changes", [
    {"primary_type_id": "kp_bnu24_math_g8_upper_1_2_t06"},
    {"primary_type_id": "模型新造题型"},
    {"secondary_type_ids": ["kp_bnu24_math_g8_upper_1_2_t06"]},
    {"secondary_type_ids": ["kp_bnu24_math_g8_upper_1_1_t01"] * 2},
    {"secondary_type_ids": ["kp_bnu24_math_g8_upper_1_1_t01", "kp_bnu24_math_g8_upper_1_1_t02", "kp_bnu24_math_g8_upper_1_1_t04"]},
    {"secondary_type_ids": ["kp_bnu24_math_g8_upper_1_1_t03"]},
    {"primary_type_id": "", "secondary_type_ids": []},
])
def test_v9_import_rejects_invalid_primary_and_secondary_types(changes: dict[str, Any]) -> None:
    from question_bank.training_criteria.analysis import ProjectionValidationError, normalize_question_type_result

    raw = _type_result()
    raw["question_type_labels"].update(changes)
    with pytest.raises(ProjectionValidationError):
        normalize_question_type_result(_type_question(), raw)


def test_v9_import_primary_is_on_every_point_and_secondary_is_never_a_link() -> None:
    from question_bank.training_criteria.analysis import ProjectionValidationError, normalize_question_type_result

    question = _type_question()
    raw = _type_result()
    normalized = normalize_question_type_result(question, raw)
    for point in normalized["solution_evidence"]["parts"][0]["evidence_points"]:
        assert [link["fine_term_id"] for link in point["fine_term_links"]] == [raw["question_type_labels"]["primary_type_id"]]
    secondary = next(item for item in question.taxonomy_contract["candidates"]["knowledge"] if item["id"].endswith("_t01"))
    raw["solution_evidence"]["parts"][0]["evidence_points"][0]["fine_term_links"] = [{
        "fine_term_id": secondary["id"], "fine_term_name": secondary["name"], "role": "direct",
    }]
    with pytest.raises(ProjectionValidationError):
        normalize_question_type_result(question, raw)


@pytest.mark.parametrize("secondary_count", [0, 1, 2, None])
def test_v9_deferred_import_roundtrip_adoption_and_new_term_review(
    tmp_path: Path, secondary_count: int | None,
) -> None:
    from types import SimpleNamespace
    from question_bank.services.question_write_service import QuestionBankWriteService
    from question_bank.current_knowledge import CurrentFineTermResolver
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.taxonomy.governance import TaxonomyGovernance
    from question_bank.training_criteria import (
        ConfigQuestionAnalysisSource, DeferredCombinedAnalysisBundle,
        DeferredCombinedProjectionWriter, DeferredCombinedQuestionAnalysisModule, QuestionAnalysisInputLoader,
    )

    database = tmp_path / "TEST-type-import.db"
    _seed_questions(database, 1)
    install_current_knowledge(database, taxonomy_revision=11)
    governance = TaxonomyGovernance(state_path=tmp_path / "TEST-taxonomy.json", knowledge_graph_db_path=database)
    loader = QuestionAnalysisInputLoader(db_path=database, data_root=tmp_path)
    plain = loader.load((1,), curriculum_volume_id="bnu24-math-g8-upper")[0]
    question = replace(plain, taxonomy_contract=governance.prompt_contract(plain.tagging_context))
    raw = _type_result()
    raw["tag_analysis"].update(taxonomy_revision=question.taxonomy_snapshot.taxonomy_revision, thought_tags=[])
    raw["tag_analysis"]["ability_tags"] = [question.taxonomy_contract["candidates"]["ability"][0]["id"]]
    if secondary_count is None:
        raw["question_type_labels"].update(primary_type_id="", secondary_type_ids=[],
            proposed_type_name="合成未收录的数学任务", reason="本题核心任务无法归入当前题型。")
    else:
        raw["question_type_labels"]["secondary_type_ids"] = [
            "kp_bnu24_math_g8_upper_1_1_t01", "kp_bnu24_math_g8_upper_1_1_t02",
        ][:secondary_count]
    resolver = CurrentFineTermResolver.from_active_database(database)
    gateway = QueueGateway([{"results": [raw]}])
    bundle = DeferredCombinedQuestionAnalysisModule(gateway=gateway,
        resolver=None if secondary_count == 1 else resolver, taxonomy_governance=governance).analyze(
        operation_id="TEST-type-import", curriculum_volume_id="bnu24-math-g8-upper",
        sources=(ConfigQuestionAnalysisSource("Q1", question),),
    )
    assert not bundle.failures
    item = bundle.items[0]
    assert item.to_dict()["schema_version"] == "deferred-combined-analysis-item-v8"
    restored = DeferredCombinedAnalysisBundle.from_checkpoint_dict(bundle.to_checkpoint_dict(), resolver=None if secondary_count == 1 else resolver)
    assert restored.items[0].question_type_labels == raw["question_type_labels"]
    tag_writer = ExistingTagProjectionWriter(
        write_service=QuestionBankWriteService(db_path=database, data_root=tmp_path),
        tagging_service=SimpleNamespace(taxonomy_governance=governance),
    )
    evidence_repository = SolutionEvidenceRepository(database)
    writer = DeferredCombinedProjectionWriter(tag_writer=tag_writer, mapping_repository=resolver,
        evidence_repository=evidence_repository, taxonomy_governance=governance)
    outcome = writer.write(restored.items[0], question=question, source_question_ref="Q1")
    assert outcome["evidence_status"] == "succeeded"
    current = evidence_repository.load_current(1, resolver=resolver, source_content_hash=item.source_content_hash)
    assert current is not None
    for part in current.parts:
        for point in part.evidence_points:
            assert [link.fine_term_id for link in point.fine_term_links] == (
                [raw["question_type_labels"]["primary_type_id"]] if secondary_count is not None else []
            )
    with connect(database) as connection:
        secondaries = connection.execute("SELECT tag_value, source FROM question_tags WHERE question_id=1 AND tag_type='secondary_type'").fetchall()
    assert {(row["tag_value"], row["source"]) for row in secondaries} == {
        (value, "taxonomy") for value in raw["question_type_labels"]["secondary_type_ids"]
    }
    from question_bank.question_types import is_type_key
    with connect(database) as connection:
        typed_tags = [row[0] for row in connection.execute("SELECT tag_value FROM question_tags WHERE question_id=1 AND tag_type='knowledge_point'") if is_type_key(row[0])]
    assert typed_tags == ([raw["question_type_labels"]["primary_type_id"]] if secondary_count is not None else [])
    if secondary_count is None:
        assert bundle.taxonomy_review_source_refs == ("Q1",)
        assert outcome["taxonomy_review_required"] is True
        assert governance.list_proposals()["items"]
        assert governance.resolve_term("knowledge", raw["question_type_labels"]["proposed_type_name"]) is None
    else:
        assert outcome["tag_status"] == "succeeded"
        # Re-adoption is idempotent; changed source content is rejected before
        # the tag writer or evidence repository receives a new write.
        writer.write(restored.items[0], question=question, source_question_ref="Q1")
        with connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM question_tags WHERE question_id=1 AND tag_type='secondary_type'").fetchone()[0] == secondary_count
        changed = replace(question, tagging_context=replace(question.tagging_context, question_text="合成题面已变化"))
        with pytest.raises(ValueError, match="source content changed"):
            writer.write(restored.items[0], question=changed, source_question_ref="Q1")
        assert evidence_repository.load_current(1, resolver=resolver, source_content_hash=item.source_content_hash).content_hash == current.content_hash
        if secondary_count == 1:
            from question_bank.training_criteria import ConfirmedQuestionAdoptionLink
            from question_bank.training_criteria.analysis import solution_evidence_source_content_hash

            with connect(database) as connection:
                connection.execute("UPDATE questions SET question_text=? WHERE id=1", (changed.tagging_context.question_text,))
            adopted = writer.adopt_linked(restored.items[0], question=changed,
                link=ConfirmedQuestionAdoptionLink("Q1", 1, "TEST-explicit-link"))
            assert adopted["evidence_status"] == "succeeded"
            assert evidence_repository.load_current(1, resolver=resolver, source_content_hash=solution_evidence_source_content_hash(changed)) is not None
    assert len(gateway.calls) == 1


@pytest.mark.parametrize("matched", [True, False])
def test_v9_bank_import_saves_both_projections_without_an_extra_model_step(tmp_path: Path, matched: bool) -> None:
    from types import SimpleNamespace
    from question_bank.services.question_write_service import QuestionBankWriteService
    from question_bank.current_knowledge import CurrentFineTermResolver
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository, SolutionEvidenceProjectionWriter
    from question_bank.taxonomy.governance import TaxonomyGovernance
    from question_bank.training_criteria import QuestionAnalysisInputLoader
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash

    database = tmp_path / "TEST-direct-type-import.db"
    _seed_questions(database, 1)
    install_current_knowledge(database, taxonomy_revision=11)
    governance = TaxonomyGovernance(state_path=tmp_path / "TEST-taxonomy.json", knowledge_graph_db_path=database)
    question = QuestionAnalysisInputLoader(db_path=database, data_root=tmp_path).load((1,), curriculum_volume_id="bnu24-math-g8-upper")[0]
    question = replace(question, taxonomy_contract=governance.prompt_contract(question.tagging_context))
    raw = _type_result()
    raw["tag_analysis"].update(taxonomy_revision=question.taxonomy_snapshot.taxonomy_revision, thought_tags=[])
    raw["tag_analysis"]["ability_tags"] = [question.taxonomy_contract["candidates"]["ability"][0]["id"]]
    if not matched:
        raw["question_type_labels"].update(primary_type_id="", secondary_type_ids=[],
            proposed_type_name="合成新数学任务", reason="核心任务无法归入现有题型。")
    gateway = QueueGateway([{"results": [raw]}])
    resolver = CurrentFineTermResolver.from_active_database(database)
    evidence_repository = SolutionEvidenceRepository(database)
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database), gateway=gateway,
        tag_writer=ExistingTagProjectionWriter(write_service=QuestionBankWriteService(db_path=database, data_root=tmp_path),
            tagging_service=SimpleNamespace(taxonomy_governance=governance)),
        evidence_writer=SolutionEvidenceProjectionWriter(mapping_repository=resolver, evidence_repository=evidence_repository, taxonomy_governance=governance),
    )
    summary = module.analyze(operation_id="TEST-bank-type-import", questions=(question,))
    assert summary["status"] == "succeeded"
    assert summary["items"][0]["training_criteria"]["question_type_labels"] == raw["question_type_labels"]
    evidence = evidence_repository.load_current(1, resolver=resolver, source_content_hash=solution_evidence_source_content_hash(question))
    assert evidence is not None
    assert all([link.fine_term_id for link in point.fine_term_links] == ([raw["question_type_labels"]["primary_type_id"]] if matched else [])
        for part in evidence.parts for point in part.evidence_points)
    with connect(database) as connection:
        secondary = [row[0] for row in connection.execute("SELECT tag_value FROM question_tags WHERE question_id=1 AND tag_type='secondary_type'")]
    assert secondary == raw["question_type_labels"]["secondary_type_ids"]
    if not matched:
        assert governance.list_proposals()["items"]
    module.analyze(operation_id="TEST-bank-type-import", questions=(question,))
    assert len(gateway.calls) == 1
    if matched:
        with connect(database) as connection:
            accepted_abilities = [row[0] for row in connection.execute("SELECT tag_value FROM question_tags WHERE question_id=1 AND tag_type='ability'")]
        retry = _type_result()
        retry.pop("tag_analysis")
        retry["question_type_labels"].update(primary_type_id="", secondary_type_ids=[],
            proposed_type_name="合成重试中新发现的数学任务", reason="重试时发现核心任务没有合适题型。")
        gateway.responses.append({"results": [retry]})
        retried = module.analyze(operation_id="TEST-type-criteria-only", questions=(question,), projection="training_criteria")
        assert retried["status"] == "succeeded"
        with connect(database) as connection:
            assert [row[0] for row in connection.execute("SELECT tag_value FROM question_tags WHERE question_id=1 AND tag_type='ability'")] == accepted_abilities
            assert connection.execute("SELECT COUNT(*) FROM question_tags WHERE question_id=1 AND tag_type='secondary_type'").fetchone()[0] == 0
        assert governance.list_proposals()["items"]
        assert len(gateway.calls) == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 1), (-5, 1), (8, 8), (100, 100), (101, 100), (999, 100),
     ("12", 12), (None, 1), ("invalid", 1), (float("inf"), 1)],
)
def test_analysis_channel_parallel_limit(value: object, expected: int) -> None:
    from types import SimpleNamespace
    from question_bank.training_criteria.analysis import gateway_parallel_limit

    assert gateway_parallel_limit(SimpleNamespace(max_parallel_requests=value)) == expected
    assert gateway_parallel_limit(SimpleNamespace()) == 1


@pytest.mark.parametrize("projection", ["tag", "all"])
def test_prompt_shares_only_identical_candidate_catalogs(projection: str) -> None:
    from question_bank.training_criteria.adapters import _combined_prompt, _prompt_candidate_contract
    from question_bank.training_criteria.analysis import PlannedAnalysisBatch

    questions = tuple(
        QuestionAnalysisInput(
            question_id=number,
            tagging_context=TaggingContext(question_text=f"计算 {number}+1", question_type="填空题"),
            taxonomy_contract={"candidates": {
                "ability": [{"id": "ability-1", "name": "运算能力"}],
                "knowledge": [{"id": f"knowledge-{scope}", "name": f"知识 {scope}"}],
            }, "taxonomy_revision": scope},
        )
        for number, scope in [(1, 1), (2, 1), (3, 2)]
    )
    batch = PlannedAnalysisBatch(questions, 1000, 1000)
    payload = json.loads(_combined_prompt(batch, projection)[1]["content"][0]["text"])
    assert len(payload["candidate_contracts"]) == 2
    for original, sent in zip(questions, payload["questions"], strict=True):
        assert sent["question_id"] == original.question_id
        assert payload["candidate_contracts"][sent["candidate_contract_ref"]] == _prompt_candidate_contract(
            original.taxonomy_contract, include_knowledge=projection != "tag",
        )
    assert payload["questions"][0]["candidate_contract_ref"] == payload["questions"][1]["candidate_contract_ref"]
    single = json.loads(_combined_prompt(PlannedAnalysisBatch(questions[:1], 1000, 1000), projection)[1]["content"][0]["text"])
    assert "candidate_contract" in single["questions"][0]
    assert "candidate_contracts" not in single


class FakeTagWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str]] = []

    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        analysis = TagAnalysis.from_dict(dict(payload))
        if not analysis.reason:
            raise ValueError("synthetic tag projection is incomplete")
        self.calls.append((question.question_id, operation_id))
        return {
            "schema_version": "tag-only-v1",
            "analysis": analysis.to_dict(),
            "model_name": model_name,
        }


class QueueGateway:
    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def analyze(
        self,
        batch,
        *,
        projection,
        operation_id,
        request_id,
    ) -> GatewayBatchResponse:
        self.calls.append(
            {
                "question_ids": batch.question_ids,
                "projection": projection,
                "operation_id": operation_id,
                "request_id": request_id,
            }
        )
        return GatewayBatchResponse(
            payload=self.responses.pop(0),
            model_name="synthetic-model",
            usage=GatewayUsage(100, 50, 150),
            latency_ms=12,
        )


class RaisingGateway:
    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def analyze(self, *_args, **_kwargs):
        self.calls += 1
        raise self.error


class SyntheticStatusError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _seed_questions(database: Path, count: int = 8) -> None:
    initialize_database(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('P4-09 合成试卷', 'completed')
            """
        ).lastrowid
        connection.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    index,
                    paper_id,
                    str(index),
                    "计算题",
                    f"合成题目 {index}",
                    f"合成答案 {index}",
                )
                for index in range(1, count + 1)
            ),
        )


def _question(
    question_id: int,
    *,
    question_type: str = "计算题",
    has_images: bool = False,
    images: tuple[QuestionAnalysisImage, ...] = (),
    text: str = "解方程 x + 1 = 2。",
) -> QuestionAnalysisInput:
    return QuestionAnalysisInput(
        question_id=question_id,
        tagging_context=TaggingContext(
            question_text=text,
            answer_text="x=1",
            question_number=str(question_id),
            question_type=question_type,
            has_images=has_images,
        ),
        rich_question_blocks=({"text": "保留上标与表格语义"},),
        images=images,
        taxonomy_contract={
            "taxonomy_revision": 7,
            "allowed_dimensions": ["knowledge", "thought", "ability"],
            "candidates": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ],
                "thought": [
                    {
                        "id": "thought_equation",
                        "name": "方程思想",
                    }
                ],
                "ability": [
                    {
                        "id": "ability_calculation",
                        "name": "运算能力",
                    }
                ],
            },
        },
    )


def _tag_payload() -> dict[str, Any]:
    return {
        "method_tags": [],
        "thought_tags": ["方程思想"],
        "ability_tags": ["运算能力"],
        "math_model_tags": [],
        "special_type_tags": [],
        "difficulty": 3,
        "predicted_error_patterns": [],
        "part_features": [
            {
                "part_id": "part-1",
                "part_label": "第1问",
                "solo": 1,
                "reasoning": 0,
                "computation": 1,
                "context": 0,
                "context_kind": "无情境",
                "hidden": 0,
                "cases": 0,
                "param_dynamic": 0,
                "trap": 0,
                "knowledge": 1,
                "evidence": "一步方程即可求出未知数",
            }
        ],
        "taxonomy_revision": 7,
        "proposed_tags": [],
        "reason": "合成标签理由",
        "confidence": 0.9,
    }


def _criteria_payload(
    question_id: int,
    *,
    points: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "training-criteria-draft-v1",
        "question_id": question_id,
        "points": points
        or [
            {
                "point_id": "p-answer",
                "target": "得到未知数的值",
                "observable_evidence": "写出 x=1",
                "equivalent_rules": ["1=x"],
                "counterexamples": [],
            }
        ],
        "auxiliary_rules": ["过程清晰但不计入判定点分母"],
        "rationale": "合成判定点",
        "confidence": 0.9,
    }


def test_combined_projection_partial_success_and_single_projection_retry(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    question = _question(1)
    invalid_criteria = {
        **_criteria_payload(1),
        "score": 5,
    }
    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": invalid_criteria,
                    }
                ]
            },
            {
                "results": [
                    {
                        "question_id": 1,
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
        ]
    )
    writer = FakeTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=writer,
    )

    partial = module.analyze(
        operation_id="p4-09-partial",
        questions=(question,),
    )
    retried = module.retry_failed_projection(
        operation_id="p4-09-partial",
        questions=(question,),
        projection="training_criteria",
    )

    assert partial["status"] == "partial"
    assert partial["items"][0]["tag_status"] == "succeeded"
    assert partial["items"][0]["criteria_status"] == "failed"
    assert retried["status"] == "succeeded"
    assert retried["items"][0]["criteria_status"] == "succeeded"
    assert retried["request_count"] == 2
    assert retried["usage"] == {
        "prompt_tokens": 200,
        "completion_tokens": 100,
        "total_tokens": 300,
    }
    assert writer.calls == [(1, "p4-09-partial")]
    assert [item["projection"] for item in gateway.calls] == [
        "both",
        "training_criteria",
    ]


def test_duplicate_operation_is_idempotent_and_conflicting_content_is_rejected(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            }
        ]
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )
    question = _question(1)

    first = module.analyze(
        operation_id="p4-09-idempotent",
        questions=(question,),
    )
    duplicate = module.analyze(
        operation_id="p4-09-idempotent",
        questions=(question,),
    )

    assert first == duplicate
    assert len(gateway.calls) == 1
    with pytest.raises(AnalysisConflictError):
        module.analyze(
            operation_id="p4-09-idempotent",
            questions=(_question(1, text="同一 operation 下被改写的题目"),),
        )


def test_interrupted_request_is_recovered_without_recalling_success(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    writer = FakeTagWriter()
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=RaisingGateway(KeyboardInterrupt()),
        tag_writer=writer,
    )
    question = _question(1)

    with pytest.raises(KeyboardInterrupt):
        module.analyze(
            operation_id="p4-09-interrupted",
            questions=(question,),
        )

    gateway = QueueGateway(
        [
            {
                "results": [
                    {
                        "question_id": 1,
                        "tag_analysis": _tag_payload(),
                    }
                ]
            },
            {
                "results": [
                    {
                        "question_id": 1,
                        "training_criteria": _criteria_payload(1),
                    }
                ]
            },
        ]
    )
    module.gateway = gateway
    resumed = module.resume_interrupted(
        operation_id="p4-09-interrupted",
        questions=(question,),
    )

    assert resumed["status"] == "succeeded"
    assert resumed["request_count"] == 3
    assert [call["projection"] for call in gateway.calls] == [
        "tag",
        "training_criteria",
    ]
    with connect(database) as connection:
        interrupted = connection.execute(
            """
            SELECT status, error_category
            FROM question_analysis_requests
            WHERE operation_id = ?
            ORDER BY created_at, request_id
            """,
            ("p4-09-interrupted",),
        ).fetchall()
    assert any(
        row["status"] == "failed" and row["error_category"] == "interrupted"
        for row in interrupted
    )


class AcceptingGovernance:
    def constrain(
        self,
        payload: Mapping[str, Any],
        *,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {
            "status": "complete",
            "taxonomy_revision": context["expected_revision"],
            "accepted_fields": {
                "method_tags": payload.get("method_tags", []),
                "thought_tags": payload.get("thought_tags", []),
                "ability_tags": payload.get("ability_tags", []),
                "math_model_tags": payload.get("math_model_tags", []),
                "special_type_tags": payload.get("special_type_tags", []),
            },
            "accepted_terms": {
                "knowledge": [
                    {
                        "id": "kp_alg_linear_equation",
                        "name": "一元一次方程",
                    }
                ]
            },
            "proposals": [],
            "notes": [],
        }


class ExistingWriterTaggingStub:
    taxonomy_governance = AcceptingGovernance()

    @staticmethod
    def taxonomy_contract(
        _context: TaggingContext,
    ) -> Mapping[str, Any]:
        return {"taxonomy_revision": 7}


class FakeProtocolResponse:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.output_text = json.dumps(payload, ensure_ascii=False)
        self.usage = {
            "input_tokens": 123,
            "output_tokens": 45,
            "total_tokens": 168,
        }


class CapturingProtocolAdapter:
    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def responses(self, **kwargs):
        self.calls.append(kwargs)
        return FakeProtocolResponse(self.payload)


# ---------------------------------------------------------------------------
# 重复 result 容错合并（多问大题模型按小问各返回一份 result 的场景）
# ---------------------------------------------------------------------------


def test_duplicate_results_merge_end_to_end_with_merge_note(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database)
    duplicate_result = {
        "question_id": 1,
        "tag_analysis": _tag_payload(),
        "training_criteria": _criteria_payload(1),
    }
    gateway = QueueGateway(
        [{"results": [dict(duplicate_result), dict(duplicate_result)]}]
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    summary = module.analyze(
        operation_id="p4-dup-merge",
        questions=(_question(1),),
    )

    item = summary["items"][0]
    assert item["tag_status"] == "succeeded"
    assert item["criteria_status"] == "succeeded"
    assert "合并" in item["merge_note"]


# ---------------------------------------------------------------------------
# 失败原因可观测（脱敏后的异常类型+短消息）
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 角平分线规则收窄（新定义题引号包裹的自定义名词不误判）
# ---------------------------------------------------------------------------

_816_STEM = (
    "（5分）在平面直角坐标系中，给出如下定义：点P到x轴、y轴的距离的较大值"
    "称为点P的“长距”，点Q到x轴、y轴的距离相等时，称点Q为“角平分线点”．"
    "（1）点A（﹣3，5）的“长距”为____；"
    "（2）若点C（﹣2，b﹣2）的长距为4，且点C在第三象限内，"
    "请判断点D（9+2b，﹣5）是否为“角平分线点”，并说明理由．"
)


class RepairQueueGateway:
    """QueueGateway variant carrying a channel retry budget.

    Captures each batch's repair_context payloads so tests can assert what
    feedback the model would actually receive.
    """

    def __init__(
        self,
        responses: list[Any],
        *,
        max_auto_retries: int = 0,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.max_auto_retries = max_auto_retries

    def analyze(
        self,
        batch: Any,
        *,
        projection: Any,
        operation_id: str,
        request_id: str,
    ) -> GatewayBatchResponse:
        self.calls.append(
            {
                "question_ids": batch.question_ids,
                "repair_contexts": [
                    dict(question.repair_context) for question in batch.questions
                ],
            }
        )
        payload = self.responses.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return GatewayBatchResponse(
            payload=payload,
            model_name="synthetic-model",
            usage=GatewayUsage(100, 50, 150),
            latency_ms=12,
        )


def test_shape_failure_budget_exhaustion_fails_without_extra_calls(
    tmp_path: Path,
) -> None:
    database = tmp_path / "question-bank.db"
    _seed_questions(database, count=1)
    gateway = RepairQueueGateway(
        [{"bad": 1}, {"bad": 2}, {"bad": 3}],
        max_auto_retries=2,
    )
    module = CombinedQuestionAnalysisModule(
        repository=CombinedAnalysisRepository(database),
        gateway=gateway,
        tag_writer=FakeTagWriter(),
    )

    module.analyze_work_items(
        operation_id="repair-exhausted",
        work_items=(QuestionAnalysisWorkItem(question=_question(1)),),
    )

    assert len(gateway.calls) == 3
    assert not gateway.responses
    repository = CombinedAnalysisRepository(database)
    assert repository.projection_status("repair-exhausted", 1, "tag") == ("failed")
    assert (
        repository.projection_status("repair-exhausted", 1, "training_criteria")
        == "failed"
    )


_OPENAI_REQUEST = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
_TRANSPORT_FAILURE_CASES = [
    (
        openai.AuthenticationError(
            "Error code: 401 - invalid api key",
            response=httpx.Response(401, request=_OPENAI_REQUEST),
            body=None,
        ),
        "authentication",
    ),
    (
        openai.RateLimitError(
            "Error code: 429 - too many requests",
            response=httpx.Response(429, request=_OPENAI_REQUEST),
            body=None,
        ),
        "rate_limit",
    ),
    (
        openai.BadRequestError(
            "Error code: 400 - unsupported parameter: response_format",
            response=httpx.Response(400, request=_OPENAI_REQUEST),
            body=None,
        ),
        "parameter_incompatible",
    ),
    (
        openai.InternalServerError(
            "Error code: 503 - service unavailable",
            response=httpx.Response(503, request=_OPENAI_REQUEST),
            body=None,
        ),
        "server_transient",
    ),
    (openai.APITimeoutError(request=_OPENAI_REQUEST), "timeout"),
    (openai.APIConnectionError(request=_OPENAI_REQUEST), "connection"),
]


@pytest.mark.parametrize(
    ("error", "expected"),
    _TRANSPORT_FAILURE_CASES,
    ids=["401", "429", "400-param", "503", "timeout", "connection"],
)
def test_transport_failures_share_one_category(
    error: BaseException,
    expected: str,
) -> None:
    from backend.llm.errors import classify_transport_error
    from question_bank.training_criteria.analysis import _error_category
    from question_bank.training_criteria.combined_analysis import (
        _analysis_error_category,
    )

    assert classify_transport_error(error).value == expected
    assert _error_category(error) == expected
    assert _analysis_error_category(error) == expected


def test_local_errors_keep_existing_categories() -> None:
    from question_bank.training_criteria.analysis import (
        GatewayResponseParseError,
        _error_category,
    )
    from question_bank.training_criteria.combined_analysis import (
        _analysis_error_category,
    )

    parse_error = GatewayResponseParseError("synthetic unparseable json payload")
    assert _error_category(parse_error) == "parse"
    assert _analysis_error_category(parse_error) == "parse"
    assert _error_category(RuntimeError("cancelled")) == "cancelled"
    assert _analysis_error_category(RuntimeError("cancelled")) == "cancelled"
    assert _error_category(ValueError("validation failed")) == "validation"
    assert (
        _analysis_error_category(
            ValueError("combined response failed the batch contract")
        )
        == "combined_response_contract"
    )


def test_deferred_authentication_error_stops_scheduling_and_retry_recovers() -> None:
    from question_bank.training_criteria import (
        ConfigQuestionAnalysisSource,
        DeferredCombinedQuestionAnalysisModule,
    )
    from tests.test_session_question_bank_sync_job import (
        _deferred_sync_contract,
        _DeferredSyncGateway,
    )

    volume = "synthetic-volume"
    sources = tuple(
        ConfigQuestionAnalysisSource(
            f"Q{index}",
            QuestionAnalysisInput(
                question_id=index,
                tagging_context=TaggingContext(
                    question_text="合成题干，请完成全部分析步骤。" * 40,
                    answer_text="42",
                    question_number=str(index),
                    question_type="解答题",
                    curriculum_volume_id=volume,
                ),
                taxonomy_contract=_deferred_sync_contract(),
            ),
        )
        for index in range(1, 4)
    )
    failing_gateway = RaisingGateway(
        openai.AuthenticationError(
            "Error code: 401 - invalid api key",
            response=httpx.Response(401, request=_OPENAI_REQUEST),
            body=None,
        )
    )
    module = DeferredCombinedQuestionAnalysisModule(gateway=failing_gateway)

    bundle = module.analyze(
        operation_id="deferred-auth-stop",
        curriculum_volume_id=volume,
        sources=sources,
    )

    # Long question texts keep every source in its own batch; the first
    # authentication failure must stop the remaining batches from being sent.
    assert failing_gateway.calls == 1
    assert not bundle.items
    assert {item.source_question_ref for item in bundle.failures} == {
        source.source_question_ref for source in sources
    }
    assert {item.category for item in bundle.failures} == {"authentication"}

    retry_module = DeferredCombinedQuestionAnalysisModule(
        gateway=_DeferredSyncGateway(empty_links=True),
    )
    retried = retry_module.retry_failed(
        bundle,
        sources=sources,
        curriculum_volume_id=volume,
    )

    assert not retried.failures
    assert len(retried.items) == len(sources)
