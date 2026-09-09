# Travel Guardian Foundation Implementation Plan

**Goal:** Integrate the existing restaurant tool loop and SQLite memory with the team Flask demo.
**Architecture:** Preserve existing response/widget contracts. Share one configurable LLM client; add a restaurant skill and SQLite session state behind browser cookies. Keep mock Macau restaurant data separate from Hangzhou map candidates.
**Tech Stack:** Python 3.12, Flask, OpenAI SDK, SQLite, existing Chroma and Amap adapters.

## Constraints
- Work on codex/integrate-travel-guardian. Preserve E:/TravelAgent and all original uncommitted files.
- Never commit .env or databases. No external bookings or automatic pushes.
- Pro/Flash version defaults remain configurable; legacy PARATERA_API_KEY remains supported.
- Weather questions produce proposals, never silently remove stops.

## Tasks
- [x] Write offline regression tests and observe failures for the new interfaces.
- [x] llm_tool.py: add call_message(messages, model, **kwargs), retain call_llm text interface; validate empty replies and configure bounded timeouts.
- [x] Copy restaurant_tools.py and original rule tests; restaurant_agent.run(message, state) executes bounded tools and stores complete successful tool history. Missing budget causes clarification; expose structured tool evidence and mock flag.
- [x] session_store.py: load/edit full state in SQLite, commit successful requests only; server.py uses opaque browser session cookies across all endpoints, restore chat via GET /session.
- [x] orchestrator_agent.py: history-aware routing, restaurant skill, preserve panels, save history; exception_agent only proposes changes. No placeholder map stop for a restaurant demo.
- [x] Preserve widget selection flow and validate submitted selections against stored options.
- [x] Update frontend startup to restore messages, README, .env.example, dependency manifest and ignores.
- [x] Run offline tests, dependency check, review diff, and record manual live-model acceptance steps.

## Validation
19 offline regression tests pass; real Paratera HTTP chat acceptance returned A at 80 MOP and no eligible restaurants at 60 MOP while retaining non-spicy and 20-minute queue constraints. SQLite reload restored both turns. JavaScript syntax and pip check pass. No real Amap or booking acceptance was performed.
