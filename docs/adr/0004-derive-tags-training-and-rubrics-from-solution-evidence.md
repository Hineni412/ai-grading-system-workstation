---
status: accepted
---

> 词表规模与维度部分已由 ADR 0007 更新；本 ADR 的“解题证据作为共同来源”决定继续有效。

# Derive tags, training criteria, and scoring steps from solution evidence

Questions are modeled as parts containing versioned, unscored solution-evidence points. Each evidence point links to fine-grained controlled terms with `direct` or `supporting_prerequisite` roles; governed mappings resolve those terms to zero, one, or multiple stable core knowledge nodes. Whole-question knowledge is a local categorized union of its evidence points rather than a second model-generated truth.

Question-bank tags, unscored training criteria, and scored exam steps are independent projections of the same evidence specification. An evidence point is already an independently scorable mathematical milestone, so each point projects one-to-one to an exam scoring step; scores are assigned only in a later whole-paper allocation. The allocation may not add, delete, merge, or reorder evidence points. This preserves the existing separation between training coverage and formal exam scores while removing three drifting analysis paths.

Milestone granularity is an analysis-model responsibility guided by complete positive, negative, atomic-step, and objective-item examples plus a pre-output checklist. Local validation intentionally does not infer mathematical step counts from transition words and does not classify wording as generic: those semantic heuristics created a second, inconsistent grading standard after a valid model response. Local validation remains authoritative for deterministic structure only: identifiers and uniqueness, contiguous ordering, earlier-point dependencies, required fields, answer-anchor source/order, forbidden score fields, and the invariant that later allocation cannot change the evidence-point structure. Exact objective items may leave `full_answer` empty and anchor evidence to `canonical_answer`; other response modes anchor to `full_answer`.

The 1121-term fine vocabulary remains the search and labeling layer, and the existing core graph remains the stable aggregation layer. Forcing every fine term into one parent node, replacing the full vocabulary with core nodes, or silently folding unmapped terms into approximate nodes were rejected because composite questions and procedural terms do not form a strict tree and incorrect mappings would corrupt mastery evidence.
