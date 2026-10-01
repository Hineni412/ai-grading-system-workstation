"""Read-only forward validation. Prints aggregate numbers, never student rows."""
from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np

from question_bank.mastery.model import MasteryModel, MasteryParameters, build_exam_observations, classify, lineage


def load_semester(data_root, volume_id):
    from backend.repositories.grading_database import open_grading_repositories
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from integration.evidence_scope import EvidenceScopeResolver
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    grading, bank = data_root / "databases/grading_system.db", data_root / "databases/question_bank.db"
    connection = sqlite3.connect(grading.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        service = DiagnosisProfileService(grading, bank, grading_db=open_grading_repositories(grading, external_connection=connection), data_root=data_root)
        scope = {"mode": "semester", "curriculum_volume_id": volume_id}
        resolved = EvidenceScopeResolver(service.db).resolve(scope={"mode": "all", "use_historical_fallback": False}, exam_scope=scope)
        sessions = sorted(resolved.sessions, key=lambda s: (str(s.get("created_at") or ""), int(s["id"])))
        ids = [int(s["id"]) for s in sessions]
        rows = service._projected_tag_evidence(student_ids=[str(s["id"]) for s in resolved.students], session_ids=ids, projection_by_session=service._tag_projections(ids))
        resolver = CurrentKnowledgeResolver.from_active_database(bank)
        observations = build_exam_observations(rows, resolver, service.mastery_session_times(exam_scope=scope))
        from question_bank.mastery.current import CurrentMasteryCalculator
        observations = CurrentMasteryCalculator(bank, resolver, data_root=data_root).model_observations(
            {"exam_scope": scope, "_mastery_observations": observations,
             "_mastery_session_times": service.mastery_session_times(exam_scope=scope)})
        parent = {r.source_key: r.target_key for r in resolver.relations if r.relation_type == "parent"}
        return observations, parent, ids, resolved.score_profiles
    finally:
        connection.close()


def metrics(observations, predictions):
    y = np.array([o["y"] for o in observations])
    p = np.clip(np.array(predictions), .02, .98)
    binary = (y == 0) | (y == 1)
    auc_p, labels = p[binary], y[binary]
    order = np.argsort(auc_p, kind="stable")
    negatives, numerator = 0., 0.
    for score in np.unique(auc_p[order]):
        group = labels[auc_p == score]
        pos, neg = group.sum(), len(group)-group.sum()
        numerator += pos*(negatives+.5*neg)
        negatives += neg
    denominator = labels.sum()*(len(labels)-labels.sum())
    calibration = []
    for low in np.arange(0, 1, .2):
        mask = (p >= low) & (p < low+.2)
        calibration.append({"range": [round(float(low), 1), round(float(low+.2), 1)], "count": int(mask.sum()),
            "prediction": round(float(p[mask].mean()), 4) if mask.any() else None,
            "achieved": round(float(y[mask].mean()), 4) if mask.any() else None})
    return {"log_loss": round(float(-(y*np.log(p)+(1-y)*np.log1p(-p)).mean()), 6),
            "brier": round(float(((p-y)**2).mean()), 6), "auc": round(float(numerator/denominator), 6) if denominator else None,
            "calibration": calibration}


def ratio_predictions(train, test):
    targets = defaultdict(list)
    abilities = defaultdict(list)
    for o in train:
        abilities[o["student"]].append(o["y"])
        for key in o["links"]:
            targets[o["student"], key].append(o["y"])
    return [sum(w*(np.mean(targets[o["student"], key]) if targets[o["student"], key] else
                  np.mean(abilities[o["student"]]) if abilities[o["student"]] else .65) for key, w in o["links"].items()) for o in test]


def tier_validation(model, test):
    values = defaultdict(list)
    week = max(o["week"] for o in test)
    supported = {(o["student"], k) for o in model.observations for k in o["links"]}
    for o in test:
        for key in o["links"]:
            if (o["student"], key) in supported:
                values[o["student"], key].append(o["y"])
    tiers, realized = Counter(), Counter()
    for (student, key), observed in values.items():
        tier = classify(*model.mastery(student, key, week), model.parameters)
        tiers[tier] += 1
        rate = float(np.mean(observed))
        realized[tier] += (rate >= .75 if tier == "stable" else rate < .60 if tier == "weak" else rate < .75 if tier == "unsteady" else False)
    return {"coverage": round(1-tiers["insufficient"]/max(len(values), 1), 4), "tiers": {
        name: {"count": tiers[name], "realized_rate": round(realized[name]/tiers[name], 4) if tiers[name] and name != "insufficient" else None}
        for name in ("stable", "unsteady", "weak", "insufficient")}}


def select_parameters(train, test, parent):
    candidates = []
    grid = list(itertools.product((.75, 1.), (.75, 1., 1.5), (.3, .5, .75), (.5, 1.), (0., .15), (0., .15), (0., .02, .05)))
    for number, (theta, kc, parents, item, tau_theta, tau_kc, slip) in enumerate(grid, 1):
        params = MasteryParameters(sigma_theta=theta, sigma_kc=kc, sigma_section=parents, sigma_chapter=parents, sigma_item=item, tau_theta=tau_theta, tau_kc=tau_kc, slip=slip)
        model = MasteryModel(parent, params).fit(train, laplace=False)
        loss = metrics(test, [model.predict(o) for o in test])["log_loss"]
        candidates.append((loss, params))
        if number % 12 == 0:
            print(json.dumps({"grid_completed": number, "grid_total": len(grid), "best_loss": min(c[0] for c in candidates)}), flush=True)
    best = min(c[0] for c in candidates)
    tied = [(loss, p) for loss, p in candidates if loss <= best+.002]
    selected_loss, selected = min(tied, key=lambda v: (-v[1].sigma_kc, v[0], v[1].tau_theta+v[1].tau_kc, v[1].slip))
    print(json.dumps({"selected_parameters": asdict(selected), "selected_loss": selected_loss, "minimum_loss": best}, ensure_ascii=False), flush=True)
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("user_data"))
    parser.add_argument("--volume", default="bnu24-math-g8-upper")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--initial", action="store_true", help="Reproduce the recorded prototype parameters")
    args = parser.parse_args()
    start = time.perf_counter()
    observations, parent, sessions, score_profiles = load_semester(args.data_root.resolve(), args.volume)
    if len(sessions) < 2:
        raise SystemExit("需要至少两次考试才能进行前向检验。")
    print(json.dumps({"observations": len(observations), "exams": len(sessions), "read_seconds": round(time.perf_counter()-start, 3)}), flush=True)
    parameters = MasteryParameters()
    if args.initial:
        parameters = replace(parameters, sigma_kc=1., sigma_section=.5, sigma_chapter=.5, sigma_item=.5)
    def split(stop):
        test = [o for o in observations if o.get("session") == sessions[stop]]
        if not test:
            raise SystemExit("测试考试没有可用观测。")
        cutoff = min(o["occurred_at"] for o in test)
        train = [o for o in observations if o.get("session") in sessions[:stop]
                 or (o["source"] == "training" and o["occurred_at"] < cutoff)]
        return train, test
    train, test = split(len(sessions)-1)
    if args.grid:
        parameters = select_parameters(train, test, parent)
    for stop in range(1, len(sessions)):
        train, test = split(stop)
        model = MasteryModel(parent, parameters).fit(train)
        ability = MasteryModel(parent, parameters, ability_only=True).fit(train, laplace=False)
        report = {"forward_exam_number": stop+1, "test_observations": len(test),
                  "new_model": metrics(test, [model.predict(o) for o in test]),
                  "ability_only": metrics(test, [ability.predict(o) for o in test]),
                  "direct_ratio": metrics(test, ratio_predictions(train, test)),
                  "tiers": tier_validation(model, test)}
        if stop == 2:
            report["recorded_v2_baseline"] = {"log_loss": .5501, "brier": .1771, "auc": .588}
        next_model = MasteryModel(parent, parameters).fit(train+test)
        identities = {(o["student"], k) for o in train for k in o["links"]}
        week = max(o["week"] for o in test)
        changes = sum(classify(*model.mastery(s, k, week), parameters) != classify(*next_model.mastery(s, k, week), parameters) for s, k in identities)
        report["tier_change_rate"] = round(changes/max(len(identities), 1), 4)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    full = MasteryModel(parent, parameters).fit(observations)
    week = max(o["week"] for o in observations)
    identities = {(o["student"], k) for o in observations for k in o["links"]}
    distribution = Counter(classify(*full.mastery(s, k, week), parameters) for s, k in identities)
    cases = []
    for reference in (.985, .21):
        known = [(s, p["score_rate"]) for s, p in score_profiles.items() if p.get("score_rate") is not None]
        if known:
            student, rate = min(known, key=lambda v: abs(v[1]-reference))
            counts = Counter(classify(*full.mastery(s, k, week), parameters) for s, k in identities if s == student)
            cases.append({"score_rate": rate, "tiers": dict(counts)})
    print(json.dumps({"full_semester_tiers": dict(distribution), "cases": cases, "parameter_version": parameters.version, "total_seconds": round(time.perf_counter()-start, 3)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
