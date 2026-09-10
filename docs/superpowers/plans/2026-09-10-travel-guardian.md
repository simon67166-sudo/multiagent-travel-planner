# Travel Guardian implementation ledger

Approved scope: all 15 skills, Macau/Hong Kong, Flask + SQLite + Paratera + Amap. Browser-visible event checks only. Owner accepts every itinerary proposal. No live OTA/XHS/FlyAI claims without providers. No vision in this release.

1. Foundation: canonical itinerary, team membership, revocable invitations, per-member preferences, immutable versions, atomic proposal acceptance, migration; HTTP integration and compatibility tests.
2. Decision core: six-role supervisor collaboration, hard constraints and fairness, food/queue/crowd/weather/lazy, sourced data and adapters, deterministic validation.
3. Experience: toilet, souvenirs, citywalk, safety, social, hidden menu, DIY, museum, photo; team/proposal/skill UI; success and unknown-data cases.
4. Demo: replayable Macau/Hong Kong flows, source labels, documented configuration/LAN instructions, regression and concurrency tests, review.

Status: foundation store implemented; HTTP integration in progress. Skill engine, source adapters, frontend are assigned disjoint files for parallel implementation. All commits stay on codex/integrate-travel-guardian.

Canonical state: {city, trip_plan, nearby_plan, mode?}; nearby_plan is a map projection in the same immutable itinerary version, not an independently writable plan. Team revision changes for member preferences/membership; itinerary version increments only on accept. Proposals capture both. Session chat history stays private.

Validation commands: python -m unittest discover -s tests -v; Node --check for each frontend script. Live providers need locally configured credentials; demo tests do not spend model quota.


2026-09-11 checkpoint: user requested an intermediate, tested commit before quota exhaustion. Foundation + integrated baseline implemented; 87 tests passing. Final scope NOT completed. See docs/guardian-checkpoint.md for exact gaps and resumption steps.
