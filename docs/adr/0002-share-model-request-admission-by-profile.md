---
status: accepted
---

# Share model request admission by saved profile

All model requests created from one saved model profile share one in-process admission limit and one RPM budget at `LLMGateway`, regardless of whether the caller is name recognition, grading, rubric generation, or question-bank tagging. Feature-specific worker pools may submit or queue work, but they cannot raise the physical request ceiling above the profile plan.

The public plans are: automatic (start at 6, recover up to 20), conservative (1), and custom (1–100 concurrent requests and 1–10,000 RPM). Provider overload or rate-limit responses reduce the effective concurrent limit and honor `Retry-After`; later successes recover gradually. This adaptation never creates an additional retry, so grading’s “failure goes to teacher review” rule remains unchanged.

RPM-only control was rejected because request frequency does not determine simultaneous in-flight work. Separate worker settings per feature were rejected because simultaneous jobs would add their limits together and make the visible value misleading. Provider capacity probing was rejected because it is not portable and would require real paid requests. The governor is process-scoped, which matches the current single-process desktop architecture; restarting clears adaptive history, and running multiple application processes is not a supported way to multiply capacity.
