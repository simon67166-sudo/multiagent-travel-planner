# Guardian UI integration

Only `orchestrator/web/guardian.js` and `guardian.css` implement this panel. The main integrator must serve both static files, link `/guardian.css`, and add `<script defer src="/guardian.js"></script>` at the bottom of the existing page. The script prepends its own section to `#schedule` without changing nearby controls, map state, or chat handlers. No framework or build step is required.

Optional chat integration: `await window.TravelGuardian?.showResult(data, {mode: 'real'})` after a successful `/chat` result. Pass `{mode: 'demo'}` for simulated results. `TravelGuardian.refresh()` refreshes team and proposals. `TravelGuardian.checkEvents({demo: false})` checks events; hidden documents never issue an event check. These methods return promises; callers should catch rejected refresh/check promises. Accepted proposals invoke `window.refreshSchedule()` if available; the main page remains responsible for rendering the confirmed itinerary and its Amap.

The page defaults to real planning. Each city has an explicit demo sample that switches the mode to demo. Automatic event checks are real, every ten visible minutes, with no catch-up request when returning from a hidden tab. Requests already in flight can finish after the page becomes hidden. Skill summaries render only public summary/status fields, never raw result objects or thought traces.

GET `/team` performs the server's legacy migration. Team creation and joining are explicit form submissions. Invite tokens are retained only in the current page closure; `join` is removed from the address bar before API calls. The server should also send `Referrer-Policy: no-referrer` on the initial invitation page (earlier-loaded page resources precede this deferred script). Fresh invitation URLs are available only to the copy action and disappear on team re-render; an existing invite is never assumed recoverable. If a copy is lost, revoke and generate another. Clipboard requires HTTPS or localhost.

Preference edits retain their loaded revision and survive background refreshes. A 409 discards the stale draft, reloads authoritative data, and asks the user to review and retry. Proposal decisions send the version shown at render time; stale acceptance is disabled. Server authorization and concurrency checks remain mandatory.

Manual verification with a running backend:

1. Load a legacy session; verify migration, full roster (including more than four members), and existing map/chat behavior.
2. Create a team, copy/revoke invitations, join in a second session, and remove a member as owner. Verify non-owners have no decision or invite controls.
3. Save dietary, budget, walking, accessibility, and all four interest values; check only the current member changed. Edit from two tabs and verify a 409 reloads state.
4. Run real planning and each city demo. Inspect public skill summaries and safe source links. Compare confirmed versus proposed itineraries, accept/reject as owner, and verify confirmed schedule refresh.
5. Check events manually; leave visible for ten minutes and inspect the request. Hide the page for ten minutes and verify no new event requests.
6. Exercise offline, timeout, 401/403/409/500, empty results, failed skills loading, keyboard navigation, and narrow screens. API strings containing HTML must appear as text; javascript/data URLs must not become links.
