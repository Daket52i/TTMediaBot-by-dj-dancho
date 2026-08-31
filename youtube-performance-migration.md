# YouTube Performance Migration

## Goal

Move YouTube Music discovery to the shared YouTube.js backend, remove `ytmusicapi`, and reduce repeat search/resolve latency without weakening cookie isolation or long-video playback.

## Tasks

- [ ] Add Node test infrastructure and behavior tests for bounded TTL/LRU caches.
- [ ] Cache and deduplicate normalized public searches for WEB and YTMUSIC modes.
- [ ] Expose YouTube.js Music song search and Up Next recommendations through the bridge.
- [ ] Migrate the Python YTM service and the YT recommendation fallback to bridge endpoints.
- [ ] Remove `ytmusicapi` and its obsolete per-bot HTTP/authentication code.
- [ ] Derive stream-cache lifetime from URL expiry with a safety margin and bounded maximum.
- [ ] Reuse valid stream resolutions locally and invalidate them after playback HTTP failures.
- [ ] Log PO-token, player request, decipher, cache, and bridge stages independently.
- [ ] Update README and changelog in separate documentation commits.
- [ ] Run unit/contract tests, rebuild through menu option 3, and verify live search, resolve, next-track, and long-video behavior.

## Done When

- [ ] `ytmusicapi` is absent from source and runtime dependencies.
- [ ] YT and YTM searches preserve current result contracts and cookie isolation.
- [ ] Repeated searches and resolutions produce measured cache hits.
- [ ] Fresh streams, prefetched next tracks, and long videos play successfully after a clean rebuild.
- [ ] Every independently reversible change is represented by a local commit; nothing is pushed.

## Notes

- Work remains on `master` by explicit user request.
- Search caches contain only public catalog metadata and may be shared across bots.
- Authenticated sessions and stream resolutions remain isolated by bot cookie identity.
