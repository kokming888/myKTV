---
name: ktv-admin-ui
description: "Design, implement, or review administrative and operational KTV interfaces: dashboard, rooms, live sessions, bookings, song catalogue, devices, customers, users and roles, reports, settings, and audit history."
---

# Administrative KTV UI

Use this skill for staff and administrator workflows. The interface should be operational, predictable, compact, information-rich, easy to scan, action-oriented, and optimized primarily for tablets and desktops.

## Required repository boundaries

- Inspect the repository before making recommendations or changes.
- Reuse existing components and preserve the frontend framework, routing, and styling systems where practical.
- Preserve API contracts, authentication, and authorization.
- Never expose administrative controls to normal customers.
- Do not create fake production data, unsupported integrations, or non-functional controls.
- Avoid unnecessary dependencies and unrelated refactoring.
- Separate business logic from presentation.
- Handle relevant loading, empty, error, offline, and unauthorized states.
- Run available validation commands; report only checks actually performed.

Only present controls and information supported by the actual application and backend. Document assumptions and unsupported capabilities. Keep live operational needs ahead of historical analytics.

## Operational dashboard and rooms

The operational dashboard should answer:

- Which rooms are occupied or available?
- Which sessions are ending soon?
- Which rooms need staff attention?
- Which devices are offline?
- Are songs failing to play or process?
- Which bookings are arriving next?

Provide a scannable room status grid with useful filtering. Room cards should show available information such as:

- Room name or number and status.
- Capacity and guest count.
- Customer or booking.
- Start time and remaining time.
- Current song and queue length.
- Device status and attention state.

Applicable statuses include Available, Reserved, Occupied, Ending soon, Cleaning, Maintenance, Offline, and Requires attention. Use text or another non-colour signal in addition to colour for every status. Room details should surface operational context and supported actions without hiding important current state.

## Sessions, bookings, catalogue, and devices

- Make live sessions, current songs, queue visibility, and sessions ending soon easy to scan.
- Show upcoming bookings and arrival timing in a way that supports staff action.
- Show catalogue availability and song processing status only when backed by actual data.
- Surface offline-device alerts and relevant device status clearly.
- Provide customer, user, and role administration only to authorized staff.
- Make reports, settings, and audit history discoverable without displacing urgent live operations.

## Tables and operational actions

Administrative tables should support relevant functions such as search, filters, sorting, pagination, clear status badges, row-level actions, and safe bulk actions. Include loading, empty, and error states and keep table actions understandable at tablet and desktop widths.

For destructive or high-impact actions:

1. State what will happen and identify affected records.
2. Check permissions before enabling or executing the action.
3. Require appropriate confirmation.
4. Wait for backend confirmation before showing success.
5. Report failure or partial success clearly.
6. Preserve auditability when supported.

Do not treat a UI-only permission check as a substitute for backend authorization.

## States and responsive behavior

Design clear loading, empty, error, offline, and unauthorized states for operational views. Avoid page-level horizontal scrolling; use responsive table patterns without obscuring critical status or actions. Keep status meaning available without colour, ensure visible keyboard focus, and make frequent controls usable on touch-enabled tablets.

