# OpenCodex gateway ownership incident during final packaging

Date boundary: 2026-08-10 23:55 through 2026-08-11 00:00, Asia/Shanghai.

## Observed facts

1. After the 16-way headless audit and its recovery turns had ended, the
   OpenCodex `/healthz` request timed out even though Bun PID 23124 still held
   `127.0.0.1:10100` in LISTEN state. This was an event-loop/health-response
   failure, not proof that the process had already exited.
2. A later health check returned healthy with PID 53300. Windows process
   creation time for that Bun process is 2026-08-10 23:55:34. Its recorded
   parent PID had already exited, so the live gateway was no longer owned by the
   original long-running service wrapper.
3. The original wrapper chain (`wscript.exe` → `cmd.exe` running
   `opencodex-service.cmd`) remained alive. Its log then recorded a repeating
   sequence roughly every five seconds: start CLI, receive `Proxy already
   running (PID 53300, port 10100)`, exit code 1, sleep, retry.
4. Codex Desktop app-server PID 21756 retained its 23:15:06 start time. WSL did
   not restart and the bounded orphan scan found zero PPID-1
   `jupyter-kernelgateway` processes in the F2 venv.
5. At the observation point the monitor showed only cross-project rollout
   estimates; no Fable headless worker remained active. Therefore this incident
   is temporally separate from the 16-worker initialization wave, although
   outstanding external requests may have contributed to gateway pressure.

## Ruling

This event is an OpenCodex/Bun gateway lifecycle and single-owner failure. It is
not a Desktop app-server crash and cannot be attributed to ipybox. The evidence
does not establish whether PID 23124 was killed by Bun, a watchdog, a tray
auto-heal path, or another launcher; the new PID's exited parent and the old
wrapper's repeated retries show that ownership was split after replacement.

No forced cleanup was performed because the monitor still estimated dozens of
external project tasks and killing PID 53300 could interrupt them. The healthy
listener was left in place.

## Proposed low-friction fix

1. Use one named ownership lock containing PID, process creation time, port and
   wrapper instance ID.
2. Before spawning, probe both process identity and `/healthz`:
   - healthy existing listener: adopt/wait; do not exit and retry every 5 s;
   - port owned but health stalled: enter a bounded drain/replace state;
   - no listener: spawn exactly one Bun child and record ownership atomically.
3. A wrapper that did not create the current listener must not kill it. It may
   become an observer and attempt ownership only after the recorded process is
   terminal.
4. Back off repeated start failures exponentially and expose `owner_pid`,
   `listener_pid`, `restart_count`, `health_stall_count` and `last_transition`
   to 8765.
5. Preserve active model requests during handoff where possible; restart is a
   last resort, not a normal response to an occupied healthy port.

## Acceptance

- Two concurrent wrapper starts produce one Bun listener and one passive
  observer, not a 5-second retry loop.
- A healthy orphan listener is adopted without interruption.
- A deliberately stalled `/healthz` transitions through a bounded state and
  produces one replacement, with no period containing two listeners.
- External requests either drain or receive an explicit retryable failure.
- Desktop app-server, WSL and ipybox process counts remain unchanged during a
  gateway ownership test.
