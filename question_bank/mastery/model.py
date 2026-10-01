"""The deterministic semester-wide hierarchical mastery model (numpy only)."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone

import numpy as np

CHINA = timezone(timedelta(hours=8))
TIER_LABELS = {"stable": "较稳定", "unsteady": "还不稳", "weak": "明显薄弱", "insufficient": "证据不足"}


@dataclass(frozen=True)
class MasteryParameters:
    formula_version: str = "mastery-v3-hierarchical-logistic"
    sigma_theta: float = 1.0
    sigma_kc: float = 1.5
    sigma_section: float = 0.75
    sigma_chapter: float = 0.75
    sigma_item: float = 1.0
    reference_difficulty: float = 3.1
    tau_theta: float = 0.0
    tau_kc: float = 0.0
    slip: float = 0.0
    stable_threshold: float = 0.75
    weak_threshold: float = 0.60
    confidence: float = 0.80

    def __post_init__(self):
        if any(v <= 0 for v in (self.sigma_theta, self.sigma_kc, self.sigma_section, self.sigma_chapter, self.sigma_item)):
            raise ValueError("mastery prior deviations must be positive")
        if self.tau_theta < 0 or self.tau_kc < 0 or not 0 <= self.slip < 1 - self.stable_threshold:
            raise ValueError("invalid mastery time/slip parameters")

    @property
    def version(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def week_of(value: datetime) -> int:
    day = value.astimezone(CHINA).date()
    return (day.toordinal() - day.weekday()) // 7


def lineage(node, parent):
    result = [node]
    while parent.get(result[-1]) and parent[result[-1]] not in result:
        result.append(parent[result[-1]])
    return result


def build_exam_observations(rows, resolver, session_times):
    """Consume the integration layer's eligible, teacher-authoritative projection."""
    observations = {}
    for row in rows:
        assessment = row.get("assessment") or {}
        session_id = int(row.get("session_id") or 0)
        occurred = session_times.get(session_id) or session_times.get(str(session_id))
        if assessment.get("eligible") is False or occurred is None:
            continue
        if isinstance(occurred, str):
            occurred = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
        if occurred.tzinfo is None:
            occurred = occurred.replace(tzinfo=CHINA)
        session, student, question = int(row["session_id"]), str(row["student_id"]), str(row["question_id"])
        difficulty = float(assessment.get("part_difficulty") or 5.5)
        def add(item, y, links):
            total = sum(links.values())
            if total <= 0:
                return
            identity = (student, item)
            observations[identity] = dict(student=student, item=item, qkey=(session, question),
                activity=("exam", session), session=session, source="exam", occurred_at=occurred,
                week=week_of(occurred), d=difficulty, y=min(max(float(y), 0), 1),
                links={k: v / total for k, v in sorted(links.items())})
        points = row.get("point_observations")
        if isinstance(points, list) and points:
            by_point = defaultdict(list)
            for point in points:
                by_point[point["point_id"]].append(point)
            for point_id, linked in by_point.items():
                links = defaultdict(float)
                for point in linked:
                    for target in resolver.resolve(point["stable_key"]):
                        links[target.stable_key] += float(point["weight"])
                add((session, question, point_id), linked[0]["achieved"], links)
            continue
        score, full = row.get("score_awarded"), row.get("full_score")
        if score is None or full is None or float(full) <= 0:
            continue
        contributions = row.get("target_contributions")
        if contributions:
            for key, (part_score, part_full) in contributions.items():
                if float(part_full) <= 0:
                    continue
                for target in resolver.resolve(key):
                    add((session, question, "t:" + target.stable_key), part_score / part_full, {target.stable_key: 1})
            continue
        targets = {t.stable_key for k in (row.get("question_tags") or {}).get("knowledge_point", []) for t in resolver.resolve(k)}
        add((session, question), float(score) / float(full), {t: 1 for t in targets})
    return list(observations.values())


def sigmoid(value):
    if isinstance(value, (int, float, np.floating)):
        return 1 / (1 + math.exp(-min(max(float(value), -700), 700)))
    return 1 / (1 + np.exp(-np.clip(value, -700, 700)))


def classify(mean, sd, parameters=MasteryParameters()):
    def probability_below(threshold):
        q = threshold / (1 - parameters.slip)
        cut = math.log(q / (1 - q))
        if sd <= 1e-12:
            return float(mean < cut)
        return 0.5 * (1 + math.erf((cut - mean) / sd / math.sqrt(2)))
    stable_below = probability_below(parameters.stable_threshold)
    if 1 - stable_below >= parameters.confidence:
        return "stable"
    if probability_below(parameters.weak_threshold) >= parameters.confidence:
        return "weak"
    if stable_below >= parameters.confidence:
        return "unsteady"
    return "insufficient"


def lbfgs(objective, initial, iterations=600, memory=10):
    x = initial.copy()
    value, grad = objective(x)
    history = []
    for _ in range(iterations):
        if np.max(np.abs(grad), initial=0) < 1e-6:
            break
        q, alphas = grad.copy(), []
        for s, y in reversed(history):
            alpha = (s @ q) / (y @ s)
            alphas.append(alpha)
            q -= alpha * y
        if history:
            s, y = history[-1]
            q *= (s @ y) / (y @ y)
        for (s, y), alpha in zip(history, reversed(alphas)):
            q += s * (alpha - (y @ q) / (y @ s))
        direction, step = -q, 1.0
        if grad @ direction >= 0:
            direction, history = -grad, []
        for _ in range(40):
            candidate = x + step * direction
            new_value, new_grad = objective(candidate)
            if new_value <= value + 1e-4 * step * (grad @ direction):
                break
            step *= 0.5
        else:
            break
        s, y = candidate - x, new_grad - grad
        if y @ s > 1e-12:
            history = (history + [(s, y)])[-memory:]
        x, value, grad = candidate, new_value, new_grad
    return x


class MasteryModel:
    def __init__(self, parent, parameters=MasteryParameters(), *, ability_only=False, dynamic_nodes=None):
        self.parent = parent
        self.parameters = parameters
        self.ability_only = ability_only
        self.dynamic_nodes = (set(dynamic_nodes) if dynamic_nodes is not None else set(parent)-set(parent.values()))

    def sigma(self, node):
        depth = len(lineage(node, self.parent))
        return (self.parameters.sigma_chapter if depth == 1 else
                self.parameters.sigma_section if depth == 2 else self.parameters.sigma_kc)

    def _param(self, key, create, sigma):
        if key not in self.index and create:
            self.index[key] = len(self.prec)
            self.prec.append(1 / sigma**2)
        return self.index.get(key)

    def _state(self, key, week, create):
        sigma = self.parameters.sigma_theta if key[0] == "theta" else self.sigma(key[2])
        indices = [(self._param(key, create, sigma), 1.0)]
        tau = self.parameters.tau_theta if key[0] == "theta" else self.parameters.tau_kc
        dynamic = key[0] == "theta" or key[2] in self.dynamic_nodes
        weeks = self.state_weeks.get(key, ())
        if tau > 0 and dynamic:
            for previous, current in zip(weeks, weeks[1:]):
                if current > week:
                    break
                indices.append((self._param((*key, "delta", current), create, tau * math.sqrt(current - previous)), 1.0))
        return [(i, c) for i, c in indices if i is not None]

    def _entries(self, observation, create):
        entries = defaultdict(float)
        student, week = observation["student"], observation["week"]
        for i, c in self._state(("theta", student), week, create):
            entries[i] += c
        if not self.ability_only:
            for node, weight in observation["links"].items():
                for ancestor in lineage(node, self.parent):
                    for i, c in self._state(("a", student, ancestor), week, create):
                        entries[i] += weight * c
        for key, sigma, coefficient in [
            (("e", observation["item"]), self.parameters.sigma_item, -1),
            (("beta0",), 10, -1),
            (("beta1",), 10, -(observation["d"] - 5.5) / 4.5),
        ]:
            index = self._param(key, create, sigma)
            if index is not None:
                entries[index] += coefficient
        if observation.get("source") == "training":
            index = self._param(("gamma",), create, 1)
            if index is not None:
                entries[index] += 1
        return list(entries.items())

    def fit(self, observations, *, iterations=600, laplace=True):
        self.observations = sorted(observations, key=lambda o: (o["week"], str(o["activity"]), o["student"], str(o["item"])))
        self.state_weeks = defaultdict(set)
        for o in self.observations:
            self.state_weeks[("theta", o["student"])].add(o["week"])
            for node in o["links"]:
                for ancestor in lineage(node, self.parent):
                    self.state_weeks[("a", o["student"], ancestor)].add(o["week"])
        self.state_weeks = {k: sorted(v) for k, v in self.state_weeks.items()}
        self.index, self.prec = {}, []
        self.rows = [self._entries(o, True) for o in self.observations]
        self._param(("beta0",), True, 10)
        self._param(("beta1",), True, 10)
        self.prec = np.array(self.prec)
        self.obs_index = np.array([r for r, row in enumerate(self.rows) for _ in row], dtype=int)
        self.par_index = np.array([i for row in self.rows for i, _ in row], dtype=int)
        self.coef = np.array([c for row in self.rows for _, c in row])
        self.y = np.array([o["y"] for o in self.observations])
        count = len(self.observations)

        def objective(x, slip):
            eta = np.bincount(self.obs_index, self.coef * x[self.par_index], minlength=count)
            q = sigmoid(eta)
            if slip == 0:
                loss = np.sum(np.logaddexp(0, eta) - self.y * eta)
                residual = q - self.y
            else:
                p = (1 - slip) * q
                loss = -np.sum(self.y * (math.log1p(-slip) - np.logaddexp(0, -eta)) + (1 - self.y) * np.log1p(-p))
                residual = (p - self.y) * (1 - q) / (1 - p)
            grad = np.bincount(self.par_index, self.coef * residual[self.obs_index], minlength=len(x))
            return loss + 0.5 * np.sum(self.prec * x*x), grad + self.prec*x

        self.x = lbfgs(lambda x: objective(x, 0), np.zeros(len(self.prec)), iterations)
        if self.parameters.slip:
            self.x = lbfgs(lambda x: objective(x, self.parameters.slip), self.x, iterations)
        self.cov = {}
        self._mastery_states = {}
        if laplace:
            self._laplace()
        return self

    def _laplace(self):
        local_indices = defaultdict(list)
        by_student = defaultdict(list)
        for key, i in self.index.items():
            if key[0] in ("theta", "a"):
                local_indices[key[1]].append(i)
        for o, row in zip(self.observations, self.rows):
            by_student[o["student"]].append((o, row))
        for student, local in local_indices.items():
            position = {i: j for j, i in enumerate(local)}
            hessian = np.diag(self.prec[local])
            for o, row in by_student[student]:
                q = float(sigmoid(sum(c*self.x[i] for i, c in row)))
                s = self.parameters.slip
                p = (1-s)*q
                curvature = (1-s)*q*(1-q)**2/(1-p) - (p-o["y"])*s*q*(1-q)/(1-p)**2
                vector = np.zeros(len(local))
                for i, c in row:
                    if i in position:
                        vector[position[i]] += c
                hessian += curvature * np.outer(vector, vector)
            self.cov[student] = position, np.linalg.inv(hessian)

    def mastery(self, student, node, week):
        identity = student, node, week
        if identity in self._mastery_states:
            return self._mastery_states[identity]
        # An unobserved node is independent of the fitted parent state. Reuse
        # that state rather than multiplying the same covariance matrix for
        # every unobserved sibling in the semester catalogue.
        if not self.ability_only and ("a", student, node) not in self.index and node in self.parent:
            mean, parent_sd = self.mastery(student, self.parent[node], week)
            result = mean, math.sqrt(parent_sd**2 + self.sigma(node)**2)
            self._mastery_states[identity] = result
            return result
        keys = [("theta", student)]
        if not self.ability_only:
            keys += [("a", student, n) for n in lineage(node, self.parent)]
        mean, variance = 0.0, 0.0
        position, cov = self.cov.get(student, ({}, None))
        vector = np.zeros(len(position))
        for key in keys:
            indices = self._state(key, week, False)
            if not indices:
                variance += (self.parameters.sigma_theta if key[0] == "theta" else self.sigma(key[2]))**2
            for i, c in indices:
                mean += c*self.x[i]
                if i in position:
                    vector[position[i]] += c
            tau = self.parameters.tau_theta if key[0] == "theta" else self.parameters.tau_kc
            dynamic = key[0] == "theta" or key[2] in self.dynamic_nodes
            weeks = self.state_weeks.get(key, ())
            if weeks and dynamic and week > weeks[-1]:
                variance += tau**2 * (week-weeks[-1])
        if cov is not None:
            variance += float(vector @ cov @ vector)
        mean -= self.x[self.index[("beta0",)]] + self.x[self.index[("beta1",)]]*(self.parameters.reference_difficulty-5.5)/4.5
        result = mean, math.sqrt(max(variance, 0))
        self._mastery_states[identity] = result
        return result

    def result(self, student, node, week):
        mean, sd = self.mastery(student, node, week)
        scale = 1-self.parameters.slip
        return {"value": float(scale*sigmoid(mean)), "interval_low": float(scale*sigmoid(mean-1.2816*sd)),
                "interval_high": float(scale*sigmoid(mean+1.2816*sd)), "tier": classify(mean, sd, self.parameters)}

    def predict(self, observation):
        eta = sum(c*self.x[i] for i, c in self._entries(observation, False))
        return float((1-self.parameters.slip)*sigmoid(eta))
