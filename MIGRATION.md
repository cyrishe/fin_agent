# Migration Baseline

- Source repository: `/Volumes/ext/stock_agent`
- Source branch: `V2.0`
- Source commit: `5de12658995126aef02dc4b9b95bb9983685d6c2`
- Target repository: `/Volumes/ext/fin_agent`
- Migration method: copy tracked application files, then validate only in the target repository

## Included

- Conversation frontend and Flask APIs
- Conversation context, planning, execution, feedback, and session state
- Finance data, file IO, search, and custom-tool modules
- Tool/Skill/Application/Agent studio modules
- Prompts, skills, finance API catalog, provider adapters, and focused tests

## Excluded

- Hotspot production scheduler and synchronization jobs
- Hotspot timeline service and production pages
- `hotspot_trace` skill, tool registration, and dedicated API routes
- Source `.env`, credentials, caches, uploads, generated artifacts, and local runtime state

The finance protocol keeps `hot_event` as a query subject because it is part of the generic financial data contract, not the removed hotspot production subsystem.

## Baseline Validation

- Flask app imports independently with 83 routes.
- Full copied test suite: `200 passed, 3 skipped`.
- Guest login, assistant page, Tool Studio, tool catalog, finance data catalog, and skill catalog returned HTTP 200 on port `22053`.
