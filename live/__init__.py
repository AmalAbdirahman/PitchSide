"""Live mode (P14): replayer -> Redpanda "events" -> worker (Redis state) -> "facts" -> API
(WebSocket, producer decisions) with an event-sourced audit log in Postgres.

The static replay in docs/ does not use any of this. The worker runs the same per-moment code as
the offline build (app/), so a full replay ends with the same facts as docs/data/hero.json.
"""
