"""掌握度分层贝叶斯模型的离线原型（2026-10-01），仅供正式实现参照。

用途与边界：
- 这是调查用的一次性脚本，不是产品代码，不要直接导入到应用里。
- 只读：先把 user_data/databases/ 下的两个数据库复制到临时目录，再对副本运行。
- 未实现方案中的时间变化、失误率、训练来源偏移和近期表现提示；这些按
  docs/requests/mastery-model-v3-plan-20261001.md 的规格实现。
- 输出只在终端显示，不要把学生身份或逐生结果写进文件、文档或日志。

用法（项目根目录，PowerShell 7 或 Git Bash）：
    $env:MASTERY_PROBE_DIR = "<临时目录>"   # 内含复制的 grading_system.db、question_bank.db
    runtime/python/python.exe docs/requests/assets/mastery-model-prototype.py extract
    runtime/python/python.exe docs/requests/assets/mastery-model-prototype.py demo
"""
from __future__ import annotations

import math
import os
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

PROBE = Path(os.environ.get("MASTERY_PROBE_DIR", ""))
LEVELS = ("kc", "sec", "ch")


# --------------------------------------------------------------------------
# 1. 只读提取：沿用现有诊断流程得到考试证据行（已应用教师锁、依赖、合格性规则）
# --------------------------------------------------------------------------
def extract(volume_id: str = "bnu24-math-g8-upper") -> None:
    sys.path.insert(0, os.getcwd())
    import integration.diagnosis_profile_service as dps
    from question_bank.mastery.current import CurrentMasteryCalculator

    cap: dict = {"rows": []}
    original = dps.DiagnosisProfileService._projected_tag_evidence

    def wrapped(self, **kwargs):
        rows = original(self, **kwargs)
        cap["rows"].extend(rows)
        return rows

    dps.DiagnosisProfileService._projected_tag_evidence = wrapped

    class Capture(CurrentMasteryCalculator):
        def _with_parent_rollups(self, direct):
            # 现行公式的逐证据贡献，用于在同一切分上精确复算现行公式作对照。
            cap["direct"] = {key: value.evidence_contributions for key, value in direct.items()}
            resolver = self.resolver
            cap["parent"] = {r.source_key: r.target_key for r in resolver.relations if r.relation_type == "parent"}
            keys = set()
            for row in cap["rows"]:
                for observation in row.get("point_observations") or []:
                    keys.add(observation["stable_key"])
                keys.update(row.get("target_contributions") or {})
                keys.update((row.get("question_tags") or {}).get("knowledge_point", []))
            cap["resolve"] = {key: [t.stable_key for t in resolver.resolve(key)] for key in keys}
            return super()._with_parent_rollups(direct)

    dps.CurrentMasteryCalculator = Capture
    service = dps.DiagnosisProfileService(
        PROBE / "grading_system.db", PROBE / "question_bank.db", data_root=Path("user_data").resolve()
    )
    service.build_tag_profiles(scope={"mode": "all"}, exam_scope={"mode": "semester", "curriculum_volume_id": volume_id})
    keep = ("session_id", "student_id", "question_id", "bank_question_id", "score_awarded", "full_score",
            "assessment", "point_observations", "target_contributions", "question_tags", "teacher_final_revision")
    cap["rows"] = [{key: row.get(key) for key in keep} for row in cap["rows"]]
    pickle.dump(cap, open(PROBE / "data.pkl", "wb"))
    print("rows", len(cap["rows"]))


# --------------------------------------------------------------------------
# 2. 观测构造：有逐点观测时一个判定点一条；否则一小问一条（得分率作 y）
# --------------------------------------------------------------------------
def load():
    data = pickle.load(open(PROBE / "data.pkl", "rb"))
    return data, data["parent"], data["resolve"]


def lineage(key, parent):
    out = [key]
    nxt = parent.get(key)
    while nxt and nxt not in out:
        out.append(nxt)
        nxt = parent.get(nxt)
    return out


def build_observations(data, resolve):
    observations = []
    for row in data["rows"]:
        assessment = row["assessment"] or {}
        if assessment.get("eligible") is False:
            continue
        session, student, question = int(row["session_id"]), str(row["student_id"]), str(row["question_id"])
        difficulty = float(assessment.get("part_difficulty") or 5.5)
        points = row["point_observations"]
        if isinstance(points, list) and points:
            by_point = defaultdict(list)
            for observation in points:
                by_point[observation["point_id"]].append(observation)
            for point_id, linked in by_point.items():
                links = defaultdict(float)
                for observation in linked:
                    for target in resolve.get(observation["stable_key"], []):
                        links[target] += float(observation["weight"])
                total = sum(links.values())
                if total > 0:
                    observations.append(dict(session=session, student=student, item=(session, question, point_id),
                                             qkey=(session, question), d=difficulty, y=float(linked[0]["achieved"]),
                                             links={k: v / total for k, v in links.items()}))
            continue
        full, score = row["full_score"], row["score_awarded"]
        if full is None or score is None or float(full) <= 0:
            continue
        contributions = row["target_contributions"] or {}
        if contributions:
            for key, (part_score, part_full) in contributions.items():
                if part_full <= 0:
                    continue
                for target in resolve.get(key, []):
                    observations.append(dict(session=session, student=student, item=(session, question, "t:" + target),
                                             qkey=(session, question), d=difficulty,
                                             y=min(max(part_score / part_full, 0.0), 1.0), links={target: 1.0}))
            continue
        targets = sorted({t for k in (row["question_tags"] or {}).get("knowledge_point", []) for t in resolve.get(k, [])})
        if targets:
            observations.append(dict(session=session, student=student, item=(session, question), qkey=(session, question),
                                     d=difficulty, y=min(max(float(score) / float(full), 0.0), 1.0),
                                     links={t: 1.0 / len(targets) for t in targets}))
    return observations


# --------------------------------------------------------------------------
# 3. 模型：logit P = θ_i + Σ_k w_k Σ_{n∈lineage(k)} a_{i,n} − (β0 + β1·dz + e_j)
# --------------------------------------------------------------------------
class Model:
    def __init__(self, parent, sig_theta=1.0, sig=(1.0, 0.5, 0.5), sig_b=0.5, ref_d=3.1):
        self.parent = parent
        self.sig_theta, self.sig, self.sig_b, self.ref_d = sig_theta, dict(zip(LEVELS, sig)), sig_b, ref_d

    def _param(self, key, create, sigma):
        if key in self.index:
            return self.index[key]
        if not create:
            return None
        self.index[key] = len(self.prec)
        self.prec.append(1.0 / sigma ** 2)
        return self.index[key]

    def _entries(self, observation, create):
        entries = defaultdict(float)
        entries[self._param(("theta", observation["student"]), create, self.sig_theta)] += 1.0
        for key, weight in observation["links"].items():
            for level, node in zip(LEVELS, lineage(key, self.parent)):
                index = self._param(("a", observation["student"], node), create, self.sig[level])
                if index is not None:
                    entries[index] += weight
        index = self._param(("e", observation["item"]), create, self.sig_b)
        if index is not None:
            entries[index] -= 1.0
        entries[self._param(("beta0",), True, 10.0)] -= 1.0
        entries[self._param(("beta1",), True, 10.0)] -= (observation["d"] - 5.5) / 4.5
        return [(i, c) for i, c in entries.items() if i is not None]

    def fit(self, observations, iterations=600, laplace=True):
        self.index, self.prec = {}, []
        rows = [self._entries(o, True) for o in observations]
        self.prec = np.array(self.prec)
        obs_index = np.array([n for n, row in enumerate(rows) for _ in row])
        par_index = np.array([i for row in rows for i, _ in row])
        coef = np.array([c for row in rows for _, c in row])
        y = np.array([o["y"] for o in observations])
        weight = np.array([o.get("w", 1.0) for o in observations])
        count = len(observations)

        def objective(x):
            eta = np.bincount(obs_index, coef * x[par_index], minlength=count)
            p = 1 / (1 + np.exp(-eta))
            loss = np.sum(weight * (np.logaddexp(0, eta) - y * eta))
            grad = np.bincount(par_index, coef * (weight * (p - y))[obs_index], minlength=len(x))
            return loss + 0.5 * np.sum(self.prec * x * x), grad + self.prec * x

        self.x = lbfgs(objective, np.zeros(len(self.prec)), iterations)
        self.observations, self.rows = observations, rows
        if laplace:
            self._laplace()
        return self

    def _laplace(self):
        """逐学生后验协方差（题目参数视为已知）。"""
        by_student = defaultdict(list)
        for observation, row in zip(self.observations, self.rows):
            by_student[observation["student"]].append((observation, row))
        self.cov = {}
        for student, items in by_student.items():
            local = sorted(i for k, i in self.index.items() if k[0] in ("theta", "a") and k[1] == student)
            position = {i: j for j, i in enumerate(local)}
            hessian = np.diag(self.prec[local])
            for observation, row in items:
                eta = sum(c * self.x[i] for i, c in row)
                p = 1 / (1 + math.exp(-eta))
                vector = np.zeros(len(local))
                for i, c in row:
                    if i in position:
                        vector[position[i]] += c
                hessian += p * (1 - p) * observation.get("w", 1.0) * np.outer(vector, vector)
            self.cov[student] = (position, np.linalg.inv(hessian))

    def _level_sigma(self, node):
        return self.sig[LEVELS[3 - len(lineage(node, self.parent))]]

    def mastery(self, student, node):
        """返回参考难度下掌握度的 logit 均值与标准差；未作答的节点使用先验方差。"""
        keys = [("theta", student)] + [("a", student, n) for n in lineage(node, self.parent)]
        position, cov = self.cov.get(student, ({}, None))
        mean, variance, selected = 0.0, 0.0, []
        for key in keys:
            index = self.index.get(key)
            if index is None:
                variance += (self.sig_theta if key[0] == "theta" else self._level_sigma(key[2])) ** 2
            else:
                mean += self.x[index]
                selected.append(position[index])
        if selected and cov is not None:
            vector = np.zeros(cov.shape[0])
            vector[selected] = 1.0
            variance += float(vector @ cov @ vector)
        mean -= self.x[self.index[("beta0",)]] + self.x[self.index[("beta1",)]] * (self.ref_d - 5.5) / 4.5
        return mean, math.sqrt(variance)

    def predict(self, observation):
        eta = sum(c * self.x[i] for i, c in self._entries(observation, False))
        return 1 / (1 + math.exp(-eta))


def lbfgs(objective, x, iterations, memory=10):
    value, grad = objective(x)
    s_list, y_list = [], []
    for _ in range(iterations):
        q = grad.copy()
        alphas = []
        for s, yv in reversed(list(zip(s_list, y_list))):
            alpha = (s @ q) / (yv @ s)
            alphas.append(alpha)
            q -= alpha * yv
        if s_list:
            q *= (s_list[-1] @ y_list[-1]) / (y_list[-1] @ y_list[-1])
        for (s, yv), alpha in zip(zip(s_list, y_list), reversed(alphas)):
            beta = (yv @ q) / (yv @ s)
            q += s * (alpha - beta)
        direction, step = -q, 1.0
        while True:
            candidate = x + step * direction
            new_value, new_grad = objective(candidate)
            if new_value <= value + 1e-4 * step * (grad @ direction) or step < 1e-10:
                break
            step *= 0.5
        s, yv = candidate - x, new_grad - grad
        if yv @ s > 1e-12:
            s_list, y_list = (s_list + [s])[-memory:], (y_list + [yv])[-memory:]
        x, value, grad = candidate, new_value, new_grad
        if np.max(np.abs(grad)) < 1e-6:
            break
    return x


def tier(mean, sd, *, slip=0.0, stable=0.75, weak=0.60, confidence=0.80):
    """较稳定 / 明显薄弱 / 还不稳 / 证据不足；掌握度 m=(1−slip)·σ(z)，z~N(mean, sd²)。"""
    phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    cut = lambda m: math.log((m / (1 - slip)) / (1 - m / (1 - slip)))
    if phi((mean - cut(stable)) / sd) >= confidence:
        return "较稳定"
    if phi((cut(weak) - mean) / sd) >= confidence:
        return "明显薄弱"
    if phi((cut(stable) - mean) / sd) >= confidence:
        return "还不稳"
    return "证据不足"


def demo() -> None:
    data, parent, resolve = load()
    observations = build_observations(data, resolve)
    train = [o for o in observations if o["session"] in (3, 4)]
    test = [o for o in observations if o["session"] == 5]
    model = Model(parent).fit(train)
    eps = 0.02
    loss = sum(-(o["y"] * math.log(min(max(model.predict(o), eps), 1 - eps))
                 + (1 - o["y"]) * math.log(1 - min(max(model.predict(o), eps), 1 - eps))) for o in test) / len(test)
    print(f"前两次考试 → 第三次考试 对数损失 {loss:.4f}（2026-10-01 数据为 0.4002）")
    keys = sorted({(o["student"], k) for o in observations for k in o["links"]})
    full = Model(parent).fit(observations)
    counts = defaultdict(int)
    for key in keys:
        counts[tier(*full.mastery(*key))] += 1
    print("全学期档位分布", dict(counts))


if __name__ == "__main__":
    {"extract": extract, "demo": demo}[sys.argv[1]]()
