---
name: ktv-customer-ui
description: "Design, implement, or review customer-facing KTV experiences: home, discovery, search, song results and details, favourites, recently played, playlists, now playing, song queue, room controls, customer navigation, and mobile/tablet responsiveness."
---

# Customer KTV UI

Use this skill for customer-facing KTV workflows and interfaces. The experience should be entertaining, visual, touch-friendly, fast, easy to learn, suitable for low-light KTV rooms, and optimized for phones, tablets, and room touchscreens.

## Required repository boundaries

- Inspect the repository before making recommendations or changes.
- Reuse existing components and preserve the frontend framework, routing, and styling systems where practical.
- Preserve API contracts, authentication, and authorization.
- Never expose administrative controls to customers.
- Do not create fake production data, unsupported integrations, or non-functional controls.
- Avoid unnecessary dependencies and unrelated refactoring.
- Separate business logic from presentation.
- Handle relevant loading, empty, error, offline, and unauthorized states.
- Run available validation commands; report only checks actually performed.

Only display controls and information supported by the actual application and backend. Document assumptions and unsupported capabilities rather than implying they exist.

## Customer navigation and primary workflow

Keep primary customer destinations to five or fewer where practical:

- Home
- Search
- Queue
- Favourites
- More

Make the primary workflow clear and continuous:

**Discover or search → select song → add to queue → receive confirmation → monitor queue**

On mobile, prefer bottom navigation, a persistent mini-player, and a dedicated Queue destination or an accessible queue bottom sheet. On landscape tablets, consider a main discovery or search area beside a persistent now-playing and queue panel.

## Discovery and search

- Make search prominent and easy to reach from customer destinations.
- Provide useful search suggestions and recent searches when supported.
- Give suitable visibility to popular songs, recently played songs, favourites, and new songs.
- Offer language categories and genre or mood categories when supported by the catalogue.
- Consider recommended duets and group songs where actual data supports them.
- Keep category, filter, and suggestion controls easy to use by touch and keyboard.

## Song results and details

Use scannable song cards or rows, preserving the existing visual system. Show available information such as:

- Artwork or a clear fallback.
- Song title and artist.
- Language and duration.
- Vocal type and song version.
- Availability.
- A clear Add action, visually primary within the result.

Song details should clarify the selected version and availability before queueing when the product supports a detail view. Do not imply metadata is available if the backend does not provide it.

## Queue and playback feedback

- Give immediate, clear feedback after adding, removing, or reordering a song.
- Handle duplicate additions according to actual product behavior; explain or confirm the result rather than silently suggesting a duplicate was queued.
- Present now-playing status distinctly from upcoming songs.
- Keep a persistent mini-player where supported and useful.
- Make queue position and estimated waiting time visible only when the application can provide meaningful values.
- Explain unavailable songs and offer a supported alternative action where one exists.
- Ensure queue changes and playback status are confirmed by the application, not presented as successful before the relevant operation completes.

## States and responsive behavior

Design clear, actionable loading, empty, error, and offline states for discovery, search, song details, favourites, and queue. Preserve entered search terms and avoid losing the customer's place after recoverable failures where feasible.

Check touch interactions and responsive layout for phones, tablets, and room touchscreens. Avoid page-level horizontal scrolling. Keep text readable in low-light settings, maintain clear contrast and focus visibility, and use appropriately sized touch targets.

