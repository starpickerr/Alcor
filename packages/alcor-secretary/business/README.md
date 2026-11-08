# Stella business services

Python 3.9+ standard-library services called by the DSH host over JSON lines.
The host owns models, sessions, MCP credentials, plugins and Agent schedules.
No retired Stella Application or Plugin Runtime is started.

Source provenance:

- `tasks`, `calendar`, `bills`, `briefing`: adapted from the matching
  first-party `Stella-Plugins/plugins/*/backend/domain` and backend entry points.
- `briefing/domain/generator.py`: adapted from `Stella-Core/agent/briefing/generator.py`;
  model requests are executed through DSH, preserving the original result validation.
- `worker.py`, `contracts.py`: DSH integration and page projections.

Imports, service injection and storage paths have been adapted. The local CLI
runner replaces the retired runtime adapter. Existing domain validation,
receipts, source provenance, conflict handling and recovery remain in these services.

Business SQLite files live in `<DSH profile>/stella/business/`. Existing preview
KV records are copied idempotently with their IDs and revisions; their originals
remain available. Historical Stella databases are not automatically imported.

AgentHub now belongs to the independent `@stella/agent-hub` developer plugin.
This worker does not open or write collaboration data. The new plugin imports
the retired collaboration database read-only; see its README for migration boundaries.

Briefing source selection is saved by the briefing page, not by Agent tools.
The host adapter reads explicitly selected native DSH plugin sources and existing
mail/eLearning MCP sources, then passes normalized items to this worker.
Credentials stay in DSH. Generated briefings send selected content to the current model.
Large source bodies can exceed the selected model's context; generation then
fails without advancing source cursors. Source data is staged for atomic commit
after model validation; a failed model attempt is recollected on retry.

Apple sync and its macOS EventKit helper belong to the independent optional
`@stella/apple-sync` bundle. It calls this worker through the host's `apple_peer`
contract; only this worker owns Tasks and Calendar business databases.
Desktop reminders require a running host and system notification availability.
Only desktop delivery is connected; other message channels are not delivered.

All Stella-authored source adaptations remain under MIT; see `STELLA-LICENSE.txt`.
DSH attribution is distributed separately in `DSH-LICENSE.txt`.
