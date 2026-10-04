"""Pair the task's first and latest pipelines on the same read-only input.

Persist generated question IDs and anonymous pair labels, never student names,
IDs, marks, answers, source text, database copies, or profile rows.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import inspect
import json
import random
from statistics import median
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import experiment_training_fit as experiment
from backend.repositories.grading_database import open_grading_repositories

VOLUME = "bnu24-math-g8-upper"
LEVELS = ["较低", "中下", "中上", "较高", "成绩资料不足"]
REASONS = {
    "no_usable_task": "没有当前可用的同目标或任务候选",
    "above_student_window": "候选高于学生难度上界",
    "below_student_window": "候选低于学生难度下界",
    "paper_difficulty_ceiling": "候选超过整卷难度上限",
    "recent_original": "可接受难度的候选属于近期原题",
    "quota": "有可用候选，但受入卷限制",
}


def load_module(path, name, *, source_path=None):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if source_path is not None:
        # Snapshot code still locates the real read-only calculation files in
        # their original repository. Snapshot hashes below remain authoritative.
        module.__file__ = str(source_path)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_pipeline(directory, label):
    projection = load_module(directory / "question_tag_projection_service.py", "integration._endpoint_projection_" + label)
    profile = load_module(directory / "diagnosis_profile_service.py", "integration._endpoint_profile_" + label,
                          source_path=ROOT / "integration/diagnosis_profile_service.py")
    profile.QuestionTagProjectionService = projection.QuestionTagProjectionService
    profile.QuestionTagProjection = projection.QuestionTagProjection
    version = tuple(hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    for name in ("diagnosis_profile_service.py", "question_tag_projection_service.py"))
    profile._profile_semantics = lambda: (label, *version)
    engine = load_module(directory / "personalized.py", "question_bank.recommendation._endpoint_" + label)
    return engine, profile


def label_for(resolver, key):
    node = resolver.node(key)
    return node.display_name if node else key


def pairs_to_ids(paper):
    return [entry["candidate"]["question_id"] for entry, _ in paper]


def own_covered(engine, paper, sid, needs):
    return {entry["key"] for _, group in paper for entry in group
            if entry["student_id"] == sid and engine._is_core(entry)
            and entry.get("practice_purpose") == "remediation" and entry["key"] in needs}


def common_audit(paper, pool, needed, recent, resolver):
    """Recheck both generated papers using the latest identical proxy rules."""
    by_question = {}
    for entry in pool:
        by_question.setdefault(entry["candidate"]["question_id"], []).append(entry)
    items, covered, full, printed = [], set(), set(), []
    for original, own_entries in paper:
        candidate = original["candidate"]
        qid = candidate["question_id"]
        entries = by_question.get(qid, [])
        passed = [entry for entry in entries if experiment.match_rejection(entry, recent) is None]
        remediation = [entry for entry in passed if experiment.engine._is_core(entry)
                       and entry.get("practice_purpose") == "remediation" and entry["key"] in needed]
        relevant = [entry for entry in entries if experiment.engine._is_core(entry)
                    and entry.get("practice_purpose") == "remediation" and entry["key"] in needed]
        priority = remediation or passed or relevant or entries
        chosen = min(priority, key=lambda e: e["distance"]) if priority else None
        plan = chosen["target"]["difficulty_plan"] if chosen else None
        native_new = bool(own_entries) and all(e.get("practice_purpose") == "new" for e in own_entries)
        new_keys = {e["key"] for e in own_entries} if native_new else set()
        new_matches = [e for e in entries if e["key"] in new_keys and e.get("selection_kind") == "direct"]
        if native_new:
            plan = original.get("target", {}).get("difficulty_plan")
        diagnostic = native_new and any(e.get("target", {}).get("diagnostic_check") for e in own_entries)
        task_state = ("diagnostic" if diagnostic else
                      "observed" if any(e.get("task_evidence_level") in {"observed_step", "observed_task"}
                                        for e in new_matches) else "unmeasured")
        difficulty = float(candidate["difficulty"])
        low = plan.get("audit_minimum", plan["minimum"]) if plan else None
        high = plan.get("audit_maximum", plan["maximum"]) if plan else None
        level = "unknown" if plan is None else "below" if difficulty < low - 1e-9 else "above" if difficulty > high + 1e-9 else "within"
        keys = {entry["key"] for entry in remediation}
        full_keys = {entry["key"] for entry in remediation if entry.get("practice_role") == "full_response"}
        covered.update(keys)
        full.update(full_keys)
        status = "补弱" if remediation else "其他可用练习" if passed else "当前规则未支持"
        items.append({"id": qid, "difficulty": difficulty, "status": status, "level": level,
                      "window": [round(low, 3), round(high, 3)] if plan else None,
                      "targets": sorted(keys), "potential_targets": sorted({entry["key"] for entry in relevant}),
                      "full_targets": sorted(full_keys),
                      "evidence": sorted({entry.get("task_evidence_level", "target_only") for entry in remediation}),
                      "original_purposes": sorted({entry.get("practice_purpose", "new") for entry in own_entries}),
                      "new_target_keys": sorted(new_keys), "new_task_state": task_state if native_new else None,
                      "diagnostic_check": diagnostic,
                      "target_aim": round(plan["aim"], 3) if plan and plan.get("aim") is not None else None,
                      "difficulty_basis": plan.get("basis", "") if plan else "",
                      "recent": qid in recent,
                      "task_labels": sorted({task["label"] for entry in remediation for task in entry["target"].get("training_tasks", [])}),
                      "task_repeated": bool(experiment.engine.paper_task_duplicates(candidate, printed))})
        printed.append(candidate)
    return {"items": items, "covered": sorted(covered), "full": sorted(full),
            "need_count": len(needed), "coverage": len(covered) / len(needed) if needed else None}


def gap_audit(paper, audit, pool, needed, recent, config):
    printed = [entry["candidate"] for entry, _ in paper]
    gaps = []
    for key in sorted(set(needed) - set(audit["covered"])):
        matches = [entry for entry in pool if entry["key"] == key and experiment.engine._is_core(entry)
                   and entry.get("practice_purpose") == "remediation"]
        available = [entry for entry in matches if experiment.match_rejection(entry, recent) is None]
        chosen = None
        if available:
            reason = "quota"
            chosen = min(available, key=lambda e: e["distance"])
        elif matches:
            rejections = {experiment.match_rejection(entry, recent) for entry in matches}
            reason = next((r for r in ("recent_original", "below_student_window", "above_student_window", "paper_difficulty_ceiling") if r in rejections), "no_usable_task")
            chosen = min((entry for entry in matches if experiment.match_rejection(entry, recent) == reason),
                         key=lambda e: e["distance"], default=None)
        else:
            reason = "no_usable_task"
        detail = {"key": key, "reason": reason}
        if chosen:
            plan = chosen["target"]["difficulty_plan"]
            detail.update(candidate=chosen["candidate"]["question_id"], difficulty=chosen["candidate"]["difficulty"],
                          window=[round(plan["audit_minimum"], 3), round(plan["audit_maximum"], 3)])
        if available:
            detail["constraints"] = sorted({reason for entry in available
                                           for reason in experiment.unselected_rejections(entry["candidate"], printed, config)})
            skills = experiment.engine._paper_skill_limit_exceeded(chosen["candidate"], printed, config)
            detail["blocking_questions"] = sorted(candidate["question_id"] for candidate in printed
                                                   if set(candidate["stable_keys"]) & skills)
            detail["blocking_questions"] = sorted(set(detail["blocking_questions"]) |
                set(experiment.engine.paper_task_duplicates(chosen["candidate"], printed)))
        gaps.append(detail)
    return gaps


def aggregate(audits):
    return {"papers": len(audits), "question_slots": sum(len(a["items"]) for a in audits),
            "remediation_slots": sum(sum(bool(item["targets"]) for item in a["items"]) for a in audits),
            "new_practice_slots": sum(sum(item["original_purposes"] == ["new"] for item in a["items"]) for a in audits),
            "diagnostic_slots": sum(sum(bool(item.get("diagnostic_check")) for item in a["items"]) for a in audits),
            "new_observed_task_slots": sum(sum(item.get("new_task_state") == "observed" for item in a["items"]) for a in audits),
            "new_related_task_slots": sum(sum(item.get("new_task_state") == "related" for item in a["items"]) for a in audits),
            "task_repeated_slots": sum(sum(item.get("task_repeated", False) for item in a["items"]) for a in audits),
            "need_count": sum(a["need_count"] for a in audits),
            "covered": sum(len(a["covered"]) for a in audits), "full": sum(len(a["full"]) for a in audits),
            "unserved": sum(a["need_count"] > 0 and not a["covered"] for a in audits),
            "level_checks": dict(Counter(item["level"] for a in audits for item in a["items"])),
            "empty": sum(not a["items"] for a in audits),
            "gaps": dict(Counter(gap["reason"] for a in audits for gap in a.get("gaps", [])))}


def write_chunks(path, value):
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for offset in range(0, len(value), 6000):
            stream.write(value[offset:offset + 6000])


def config_fingerprint(data):
    configs = sorted((data / "config/uploaded").glob("*.json"))
    return len(configs), hashlib.sha256(b"".join(p.name.encode()+b"\0"+p.read_bytes() for p in configs)).hexdigest()


def run(output, *, baseline_remediation_only=False, max_unmeasured_questions=0,
        baseline_max_unmeasured_questions=0, include_unmeasured_stage=False, personal_only=False):
    start = time.perf_counter()
    engines, profiles = zip(*(load_pipeline(output / folder, folder) for folder in ("baseline", "latest")))
    intermediate = load_pipeline(output / "unmeasured", "unmeasured")[0] if include_unmeasured_stage else None
    if intermediate:
        for name in ("_candidate_entries", "evaluate_candidates"):
            assert inspect.getsource(getattr(intermediate.PersonalizedRecommendationModule, name)) == inspect.getsource(getattr(engines[1].PersonalizedRecommendationModule, name)), "Stage matching differs; pools cannot be reused"
    data = ROOT / "user_data"
    configuration_before = config_fingerprint(data)
    gp, bp = (data / "databases" / name for name in ("grading_system.db", "question_bank.db"))
    report = {"versions": [engine.ENGINE_VERSION for engine in engines], "scopes": [], "catalog": {},
              "method": "Current identical raw inputs; both papers rechecked with latest target/task/difficulty rules. This is a rule audit, not independent learning efficacy.",
              "model_requests": 0, "database_writes": 0, "raw_student_exports": 0}
    report["group_comparison"] = not personal_only
    report["selection_settings"] = {"baseline_remediation_only": baseline_remediation_only,
                                   "baseline_max_unmeasured_questions": baseline_max_unmeasured_questions,
                                   "latest_max_unmeasured_questions": max_unmeasured_questions}
    if intermediate:
        report.update(intermediate_version=intermediate.ENGINE_VERSION, stage_shared_evaluation_verified=True)
    with experiment.readonly_runtime() as reading:
        with reading(gp) as grading, reading(bp) as bank:
            versions = [connection.execute("PRAGMA data_version").fetchone()[0] for connection in (grading, bank)]
            fingerprints = [profiles[1]._database_content_revision(path, connection) for path, connection in ((gp, grading), (bp, bank))]
            services, diagnoses, modules, masteries, activities = [], [], [], [], []
            for i, (engine, profile_module) in enumerate(zip(engines, profiles)):
                service = profile_module.DiagnosisProfileService(gp, bp,
                    grading_db=open_grading_repositories(gp, external_connection=grading),
                    question_bank_connection=bank, data_root=data, persist_snapshots=False)
                with patch.object(service, "_read_local_profile", return_value=None):
                    diagnosis = engine._normalize_diagnosis(service.build_profiles(
                        scope={"mode": "all", "use_historical_fallback": False},
                        exam_scope={"mode": "semester", "curriculum_volume_id": VOLUME}))
                module = engine.PersonalizedRecommendationModule(db_path=bp, data_root=data, semester_mastery=service.semester_mastery)
                services.append(service); diagnoses.append(diagnosis); modules.append(module)
                masteries.append(module._mastery_snapshot(diagnosis))
                activities.append(service.graded_activities(tuple(s["student_id"] for s in diagnosis["students"])))
                experiment.progress("endpoint_diagnosis_ready", endpoint=i, students=len(diagnosis["students"]), seconds=round(time.perf_counter()-start, 2))
            assert {p["student_id"]: p.get("score_rate") for p in diagnoses[0]["students"]} == {p["student_id"]: p.get("score_rate") for p in diagnoses[1]["students"]}
            students = diagnoses[1]["students"]
            known = sorted((p for p in students if isinstance(p.get("score_rate"), (int, float))), key=lambda p: (p["score_rate"], p["student_id"]))
            tiers = {p["student_id"]: min(3, i*4//len(known)) for i, p in enumerate(known)}
            aliases = {}
            rng = random.Random(20261003)
            for tier in range(5):
                ids = sorted(p["student_id"] for p in students if tiers.get(p["student_id"], 4) == tier)
                rng.shuffle(ids)
                aliases.update({sid: f"{LEVELS[tier]}-{i+1:02d}" for i, sid in enumerate(ids)})
            resolver = modules[1].current_knowledge
            metadata = [module._source_practice_metadata(diagnosis) for module, diagnosis in zip(modules, diagnoses)]
            relations = tuple({"relation_type": r.relation_type, "source_key": r.source_key, "target_key": r.target_key} for r in resolver.relations)
            recent = [module._recent_question_ids(tuple(aliases), diagnosis=diagnosis, graded_activities=events, recent_activity_count=3)
                      for module, diagnosis, events in zip(modules, diagnoses, activities)]
            assert recent[0] == recent[1]
            # Scope processing is appended below; all selected paper data remain in memory until anonymized.
            scope_results = process_scopes(engines, modules, diagnoses, masteries, metadata, activities, recent, students, aliases, tiers, resolver, relations,
                baseline_remediation_only=baseline_remediation_only, max_unmeasured_questions=max_unmeasured_questions,
                baseline_max_unmeasured_questions=baseline_max_unmeasured_questions, intermediate=intermediate,
                personal_only=personal_only)
            report["scopes"] = scope_results
            all_keys = {key for scope in scope_results for key in scope["target_keys"]}
            report["catalog"] = {key: label_for(resolver, key) for key in sorted(all_keys)}
            assert versions == [connection.execute("PRAGMA data_version").fetchone()[0] for connection in (grading, bank)]
            assert fingerprints == [profiles[1]._database_content_revision(path, connection) for path, connection in ((gp, grading), (bp, bank))]
            report["input"] = {"grading": fingerprints[0], "bank": fingerprints[1], "same_student_scores": True, "same_recent_history": True, "same_read_versions": True}
    configuration_after = config_fingerprint(data)
    assert configuration_before == configuration_after, "Exam configuration changed during comparison"
    report["input"].update(exam_config_count=configuration_after[0], exam_config_hash=configuration_after[1])
    original = ROOT / "output/test_training_fit_20261002/aggregate-report.json"
    if original.exists():
        historical = json.loads(original.read_text(encoding="utf-8"))
        report["historical_first_run"] = [{"scope": str(c["chapter"]), "native": c["native"]} for c in historical["chapters"]]
    elif (output / "comparison.json").exists():
        report["historical_first_run"] = json.loads((output / "comparison.json").read_text(encoding="utf-8")).get("historical_first_run", [])
    report["source_hashes"] = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest() for folder in ("baseline", "latest") for path in (output / folder).glob("*.py")}
    report["seconds"] = round(time.perf_counter()-start, 2)
    write_chunks(output / "comparison.json", json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    experiment.progress("endpoint_comparison_saved", seconds=report["seconds"])
    return report


def process_scopes(engines, modules, diagnoses, masteries, metadata, activities, recent,
                   students, aliases, tiers, resolver, relations, *, baseline_remediation_only=False,
                   max_unmeasured_questions=0, baseline_max_unmeasured_questions=0, intermediate=None, personal_only=False):
    results = []
    for chapters in ((1,), (2,), (1, 2)):
        keys = tuple(f"kp_bnu24_math_g8_upper_{chapter}" for chapter in chapters)
        configs = [engine.PersonalizedRecommendationConfig(scope_keys=keys, curriculum_volume_id=VOLUME,
                   **({"remediation_only": True} if i or baseline_remediation_only else {}),
                   **({"max_unmeasured_questions": max_unmeasured_questions if i else baseline_max_unmeasured_questions}
                      if "max_unmeasured_questions" in engine.PersonalizedRecommendationConfig.__dataclass_fields__ else {})) for i, engine in enumerate(engines)]
        leaves = [engine._scope_leaves(keys, diagnosis=diagnosis, relations=relations) for engine, diagnosis in zip(engines, diagnoses)]
        owned = engines[1]._scope_descendants(frozenset(keys), resolver)
        needs = [{sid: {key: value for key, value in points.items() if key in owned}
                  for sid, points in engine._group_needs(diagnosis, leaf, cap=8).items()}
                 for engine, diagnosis, leaf in zip(engines, diagnoses, leaves)]
        candidates, source_relations, _ = modules[1]._source_snapshot(knowledge_keys=leaves[1], candidate_config=configs[1])
        old_candidates, _, _ = modules[0]._source_snapshot(knowledge_keys=leaves[0], candidate_config=configs[0])
        assert {c["question_id"] for c in old_candidates} == {c["question_id"] for c in candidates}, "Candidate raw inputs differ"
        pools = [module.evaluate_candidates(diagnosis=diagnosis, config=config, candidates=candidates,
                  mastery=mastery, source_metadata=source, recent=history, graded_activities=events)["pools"]
                 for module, diagnosis, config, mastery, source, history, events in
                 zip(modules, diagnoses, configs, masteries, metadata, recent, activities)]
        native, selection_timings = [], []
        for engine, config, evaluated in zip(engines, configs, pools):
            papers, timings = {}, []
            for sid, pool in evaluated.items():
                started = time.perf_counter()
                papers[sid] = engine._choose_practice_entries(pool, 10, config)
                timings.append(time.perf_counter() - started)
            ordered = sorted(timings)
            native.append(papers)
            selection_timings.append({"students": len(timings), "total_seconds": round(sum(timings), 4),
                "median_seconds": round(median(timings), 4) if timings else None,
                "p95_seconds": round(ordered[max(0, (95*len(ordered)+99)//100-1)], 4) if ordered else None})
        intermediate_papers = ({sid: intermediate._choose_practice_entries(pool, 10,
            intermediate.PersonalizedRecommendationConfig(**configs[1].to_dict())) for sid, pool in pools[1].items()} if intermediate else None)
        parity = []
        for i, module in enumerate(modules):
            draft = module._build_draft(diagnosis=diagnoses[i], config=configs[i], candidates=candidates,
                      relations=source_relations, mastery=masteries[i], recent=recent[i], excluded_question_ids=set())
            same = all({item["question_id"] for item in row["items"]} == set(pairs_to_ids(native[i][row["student_id"]])) for row in draft["students"])
            assert same, "Replay differs from native draft builder"
            parity.append(same)
        experiment.progress("endpoint_personal_papers_ready", chapters=chapters, native_parity=parity)
        broad_config = replace(configs[1], difficulty_max=10)
        broad_candidates, _, _ = modules[1]._source_snapshot(knowledge_keys=leaves[1], candidate_config=broad_config)
        original_plan = engines[1]._difficulty_plan
        def broad_plan(ref, score_rate, cap, target=None, profile=None, **kwargs):
            plan = original_plan(ref, score_rate, 8, target, profile, **kwargs)
            return {**plan, "audit_minimum": plan["minimum"], "audit_maximum": plan["maximum"], "minimum": 1, "maximum": 10}
        with patch.object(engines[1], "_difficulty_plan", broad_plan):
            broad_pools = modules[1].evaluate_candidates(diagnosis=diagnoses[1], config=broad_config,
                          candidates=broad_candidates, mastery=masteries[1], source_metadata=metadata[1],
                          recent={sid: set() for sid in aliases}, graded_activities=activities[1])["pools"]
        papers, audits, intermediate_audits = [], [[], []], []
        changed = retained_slots = added_slots = removed_slots = gained = lost = 0
        target_keys = set()
        for profile in students:
            sid = profile["student_id"]
            current_need = needs[1].get(sid, {})
            old_need = needs[0].get(sid, {})
            target_keys.update(set(current_need) | set(old_need))
            checks = [common_audit(native[i][sid], broad_pools[sid], current_need, recent[1].get(sid, set()), resolver) for i in range(2)]
            checks[1]["gaps"] = gap_audit(native[1][sid], checks[1], broad_pools[sid], current_need, recent[1].get(sid, set()), configs[1])
            assert set(checks[1]["covered"]) == own_covered(engines[1], native[1][sid], sid, current_need), "Common check differs from latest native coverage"
            old_ids, new_ids = [set(pairs_to_ids(paper[sid])) for paper in native]
            retained, removed, added = old_ids & new_ids, old_ids - new_ids, new_ids - old_ids
            before, after = [set(check["covered"]) for check in checks]
            gained += len(after-before); lost += len(before-after)
            changed += old_ids != new_ids
            retained_slots += len(retained); removed_slots += len(removed); added_slots += len(added)
            record = {"label": aliases[sid], "tier": tiers.get(sid, 4), "before": checks[0], "after": checks[1],
                      "old_need_count": len(old_need), "current_need_count": len(current_need),
                      "retired_needs": sorted(set(old_need)-set(current_need)), "new_needs": sorted(set(current_need)-set(old_need)),
                      "retained_needs": sorted(set(old_need)&set(current_need)),
                      "retained_questions": sorted(retained), "removed_questions": sorted(removed), "added_questions": sorted(added)}
            if intermediate_papers is not None:
                record["intermediate"] = common_audit(intermediate_papers[sid], broad_pools[sid], current_need, recent[1].get(sid, set()), resolver)
                intermediate_audits.append(record["intermediate"])
            target_keys.update(key for audit in checks for item in audit["items"] for key in item.get("new_target_keys", []))
            if hasattr(engines[1], "paper_task_duplicates"):
                assert not any(item["task_repeated"] for item in checks[1]["items"]), "A repeated task entered the latest paper"
            assert sum(item["original_purposes"] == ["new"] for item in checks[1]["items"]) <= max_unmeasured_questions, "Unmeasured limit exceeded"
            papers.append(record)
            for i in range(2): audits[i].append(checks[i])
        scope = {"chapters": list(chapters), "candidate_count": len(candidates), "native_parity": parity,
                 "personal_selection_timings": selection_timings,
                 "personal": [aggregate(values) for values in audits], "papers": sorted(papers, key=lambda row: (row["tier"], row["label"])),
                 "changed_papers": changed, "retained_slots": retained_slots, "removed_slots": removed_slots, "added_slots": added_slots,
                 "coverage_gained": gained, "coverage_lost": lost,
                 "own_needs": [sum(len(value) for value in n.values()) for n in needs],
                 "own_coverage": [sum(len(own_covered(engine, native[i][sid], sid, needs[i].get(sid, {}))) for sid in aliases) for i, engine in enumerate(engines)],
                 "by_tier": [{"name": name, "before": aggregate([p["before"] for p in papers if p["tier"] == tier]),
                              "after": aggregate([p["after"] for p in papers if p["tier"] == tier])} for tier, name in enumerate(LEVELS)],
                 "groups": [[], []]}
        group_audits = [[], []]
        if intermediate_audits:
            scope["intermediate"] = aggregate(intermediate_audits)
        for i, (engine, module, diagnosis, config) in enumerate(zip(engines, modules, diagnoses, configs)):
            if personal_only:
                break
            grouping = module.chapter_groups(diagnosis=diagnosis,
                       config=replace(config, paper_mode="shared", group_scope_keys=keys, **({"remediation_only": False} if i else {})),
                       graded_activities=activities[i])
            for group_index, group in enumerate(grouping.get("groups", [])):
                members = [member["student_id"] for member in group["members"]]
                shared_config = replace(config, paper_mode="shared", target_keys=tuple(t["knowledge_key"] for t in group["targets"]),
                                        **({"remediation_only": False} if i else {}))
                scoped = {**diagnosis, "students": [p for p in diagnosis["students"] if p["student_id"] in members]}
                evaluated = module.evaluate_candidates(diagnosis=scoped, config=shared_config, candidates=candidates,
                            mastery=masteries[i], source_metadata=metadata[i], recent=recent[i], graded_activities=activities[i])
                common = engine._common_entries([entry for pool in evaluated["pools"].values() for entry in pool], members)
                paper = engine._choose_practice_entries(common, 10, shared_config)
                if i and hasattr(engine, "paper_task_duplicates"):
                    printed = []
                    for entry, _ in paper:
                        assert not experiment.engine.paper_task_duplicates(entry["candidate"], printed), "A repeated task entered the shared paper"
                        printed.append(entry["candidate"])
                member_checks = [common_audit(paper, broad_pools[sid], needs[1].get(sid, {}), recent[1].get(sid, set()), resolver) for sid in members]
                checks = aggregate(member_checks)
                checks["printed_questions"] = len(paper)
                coverage = [a["coverage"] for a in member_checks if a["coverage"] is not None]
                group_audits[i].extend(member_checks)
                scope["groups"][i].append({"label": f"{'初版' if i == 0 else '最新'}组-{group_index+1:02d}",
                    "members": sorted(aliases[sid] for sid in members), "question_ids": pairs_to_ids(paper), "summary": checks,
                    "coverage_range": [round(min(coverage), 4), round(max(coverage), 4)] if coverage else None,
                    "tier_counts": dict(Counter(LEVELS[tiers.get(sid, 4)] for sid in members)),
                    "member_checks": [{"label": aliases[sid], "covered": len(check["covered"]), "needs": check["need_count"],
                                       "above": sum(item["level"] == "above" for item in check["items"]),
                                       "unknown": sum(item["level"] == "unknown" for item in check["items"])} for sid, check in zip(members, member_checks)]})
        scope["group_summary"] = [aggregate(values) for values in group_audits]
        scope["target_keys"] = sorted(target_keys)
        results.append(scope)
        experiment.progress("endpoint_scope_done", chapters=chapters, old_coverage=scope["personal"][0]["covered"],
                            current_coverage=scope["personal"][1]["covered"], changed_papers=changed,
                            old_groups=len(scope["groups"][0]), current_groups=len(scope["groups"][1]))
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-remediation-only", action="store_true")
    parser.add_argument("--max-unmeasured-questions", type=int, default=0)
    parser.add_argument("--baseline-max-unmeasured-questions", type=int, default=0)
    parser.add_argument("--include-unmeasured-stage", action="store_true")
    parser.add_argument("--personal-only", action="store_true", help="Recheck individual papers only; leave group comparison explicitly empty")
    args = parser.parse_args()
    run(args.output.resolve(), baseline_remediation_only=args.baseline_remediation_only,
        max_unmeasured_questions=args.max_unmeasured_questions,
        baseline_max_unmeasured_questions=args.baseline_max_unmeasured_questions,
        include_unmeasured_stage=args.include_unmeasured_stage, personal_only=args.personal_only)
