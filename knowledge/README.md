# Version-controlled project knowledge

`fictional_lantern.json` is the sole knowledge store for this milestone. Its
Lantern Board objectives and approvals are **fictional demonstration content**,
not H2O requirements, HHC information, or actual stakeholder approval. Nothing
is learned or written automatically by an agent. Git preserves its history.

Each entry contains:

| Field | Meaning |
| --- | --- |
| `id`, `revision` | Stable identifier and incrementing entry revision |
| `kind` | Vision, module objective, UI/architecture principle, priority, decision, limitation, or exclusion |
| `status` | `approved`, `assumption`, `suggestion`, or `unverified` |
| `text` | The objective, decision with rationale, or contextual statement |
| `source` | Document identifier, source revision, and locator; never fetched |
| `revised_on` | Revision date (`YYYY-MM-DD`) |
| `approval` | Approver and matching source revision for approved entries; `null` otherwise |
| `modules`, `paths`, `dependencies` | Documented associations; paths are exact relative names, not files to open |

Only `approved` entries may be used as goals in alignment assessments. Other
statuses remain visible in a separate non-authoritative context list. An approved
entry must have approval provenance matching its current source revision;
duplicate IDs, unknown statuses, extra fields, unknown module dependencies, and
unsupported schema versions fail validation. Conflicting approved requirements
still require human resolution; schema validation cannot detect every conflict.

To revise knowledge, have a person check the source, edit the tracked JSON,
increment each changed entry's `revision` and the store's `revision`, and update
its source/date/approval metadata. New ideas start as suggestions or assumptions;
do not mark them approved until the relevant owner approves them. Submit the
knowledge change through normal PR review. There is no model-accessible update
operation. Local schema validation checks metadata consistency, not the identity
or honesty of an approver; trust rests on the reviewed Git history.

Reports include the complete knowledge snapshot, its canonical SHA-256, and
citations such as `knowledge:booking@1`, so later edits cannot silently change
what a saved report evaluated. Replacing fictional knowledge with actual project
knowledge, handling multiple projects, migrations, and retention policy require
a separate scoped milestone and human approval. Do not add credentials or
restricted internal material to knowledge or diffs.
