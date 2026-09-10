
# Supervisor Lifecycle Audit (FA06)

## 1. Overview
The zloop.supervisor (M6) manages end-to-end wave execution. It claims a controller token using a CAS mechanism on uns.controller_nonce and enforces strict serialization and liveness.

## 2. Key Paths & Logic
- **Entry (un_wave)**: Validates kimi web server status (D-17), probes own process start time (D-20), and claims the controller token.
- **Loop (_supervise)**: Executes the main event loop, which handles:
    - External cancellation (D-8).
    - Dependent packet scheduling (PENDING -> RUNNING based on dependencies, I8).
    - Result collection and validation (I6 fence).
    - Packaging/Materialization (M7 module).
- **Exit**: Always releases the controller token in a inally block.

## 3. Critical Gates & Abort Conditions
- **Gate: KIMI_SERVER_UP**: Aborts if http://127.0.0.1:58627/api/v1/healthz answers (D-17/P-SEC1), meaning a loopback escalation path is open.
- **Controller Conflict**: Aborts if the controller token is busy (cannot claim or takeover fails).
- **Stalled Exit**: If the loop makes no progress and packets are still RUNNING or PENDING, returns stalled.
- **Validation Failures**: Stage is not EXECUTING or base is dirty (I37).

## 4. Stability Analysis
- **Liveness**: The supervisor does not busy-poll; it uses 	ime.sleep(poll_s) when no forward progress is made.
- **Deadlocks/Loops**: The loop is finite: it advances state until all packets are in TERMINAL_PACKET_STATES or stalled.
- **Race conditions**: Handled by CAS on controller token and D-20 takeover logic (proving old owner dead).

## Evidence
- Direct Evidence: src/zloop/supervisor.py lines 122-198 (un_wave and controller claim).
- Direct Evidence: src/zloop/supervisor.py lines 231-314 (_supervise loop and stall logic).
- Direct Evidence: src/zloop/supervisor.py lines 71-88 (kimi_server_up gate).
