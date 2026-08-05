---
status: accepted
---

# Use metadata-only shared tasks and domain-owned AI content

Teaching preparation and class-teacher AI operations use one shared `WorkspaceAITask` module above the existing JobManager. Job remains an internal runtime projection for queueing, progress and cancellation; A and B do not treat JobRecord or its payload/result as their product contract. Shared task and Handoff metadata live beside the existing JobStore in `grading_system.db` under the public `grading` migration family. This store contains only opaque domain references, revisions, request and destination fingerprints, fixed task-kind labels, send-attempt evidence, safe error codes and Handoff adoption projections. It never contains prompts, responses, student names, teacher messages, lesson material content or real file paths.

Complete inputs, model results and proposals remain in the owning A or B database. Shared Gateway diagnostics for workspace tasks are forced into metadata-only mode; an Adapter cannot turn shared body logging back on. Class-teacher requests are sent without a PIN gate or anonymization, as explicitly chosen for B-UI-R7, but that user-facing simplification does not widen the shared logging boundary.

An operation reserves at most one send attempt before crossing the network boundary. Because a process can stop between the durable reservation and the actual socket write, the shared task records `not_started`, `may_have_started` or `response_persisted` rather than claiming an exact billable call count. A `may_have_started` operation is never automatically resent. A pre-dispatch failure with zero attempts may be explicitly requeued under the same operation and unchanged fingerprints.

One model task may produce several Handoffs. Each Handoff has its own stable adoption ID and lifecycle; the task only aggregates how many are pending, adopted, discarded or stale. A/B Adapters write the formal domain object and an Adoption Receipt in one transaction in their own database. If the process stops before the shared metadata is updated, replay queries the domain receipt and converges without creating a second record. One Handoff cannot span two domain databases; cross-target follow-up work is represented by separate Handoffs.

Storing full bodies in the shared Job payload was rejected because normal Job APIs, diagnostics and task lists would copy student or teaching content outside its domain. A new shared content database was rejected because it would create another retention, backup and deletion authority. Treating the send reservation as an exact physical call count was rejected because the local process cannot prove that fact across the final pre-network crash window. A task-level adopted flag was rejected because one class-teacher turn can produce multiple independently confirmed work items.

This ADR supersedes the shared-call logging, per-request privacy-preview and task-recovery parts of ADR 0005 for B-UI-R7. ADR 0005 remains historical evidence for the earlier protected-vault design; it no longer defines the current class-teacher interaction gate or anonymization behavior.
