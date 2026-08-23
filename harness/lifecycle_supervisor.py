#!/usr/bin/env python3
"""Zero-model lifecycle supervisor for one ``codex exec`` worker.

The dispatcher starts one supervisor per packet and then exits.  The
supervisor owns the child handle for its complete lifetime, records a durable
roster entry, waits without LLM polling, turns non-zero exits into
``exec_failed`` events, and terminates the whole process boundary on timeout
or a parent Stop cancellation request.

Desktop-native subagents are deliberately out of scope: hooks can reconcile
their shadow roster and request a host ``close_agent`` call, but only the
Codex host runtime can release those native slots.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Sequence


POLL_SECONDS = 0.20
HEARTBEAT_INTERVAL_S = 10.0
TERMINAL_STATES = {"completed", "failed", "timed_out", "cancelled", "spawn_failed"}
ATOMIC_REPLACE_TIMEOUT_S = 5.0
ATOMIC_REPLACE_INITIAL_BACKOFF_S = 0.005
ATOMIC_REPLACE_MAX_BACKOFF_S = 0.100
LOCK_ACQUIRE_TIMEOUT_S = 60.0


class LifecycleError(RuntimeError):
    pass


@contextlib.contextmanager
def locked(path: Path, *, timeout_s: float = LOCK_ACQUIRE_TIMEOUT_S):
    """Cross-process one-byte advisory lock; the lock file is persistent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + max(0.0, timeout_s)
            backoff = ATOMIC_REPLACE_INITIAL_BACKOFF_S
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise LifecycleError(
                            "lifecycle lock remained busy for %.3fs: %s"
                            % (timeout_s, exc)) from exc
                    time.sleep(backoff)
                    backoff = min(ATOMIC_REPLACE_MAX_BACKOFF_S, backoff * 2)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_json(path: Path, value: Any, *,
                replace_timeout_s: float = ATOMIC_REPLACE_TIMEOUT_S) -> None:
    """Durably replace one JSON document, tolerating transient Windows holds.

    The roster read/modify/write transaction is already serialized by the
    persistent advisory lock.  On Windows, however, antivirus/indexing and a
    reader without delete sharing can briefly make ``os.replace`` return
    ``WinError 5`` even though no second writer owns the roster lock.  Treat
    that as transient for a small bounded interval; never drop the lock or
    rewrite the destination in-place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".%d.tmp" % os.getpid())
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + max(0.0, replace_timeout_s)
        backoff = ATOMIC_REPLACE_INITIAL_BACKOFF_S
        while True:
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    raise LifecycleError(
                        "atomic roster replace remained blocked for %.3fs: %s"
                        % (replace_timeout_s, exc)) from exc
                time.sleep(backoff)
                backoff = min(ATOMIC_REPLACE_MAX_BACKOFF_S, backoff * 2)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.root = data_dir / "lifecycle"
        self.roster_path = self.root / "exec_roster.json"
        self.lock_path = self.root / ".exec_roster.lock"
        self.events_path = data_dir / "events.ndjson"
        self.cancel_dir = self.root / "cancel"

    def update(self, packet_id: str, *, run_id: str | None = None,
               attempt: int | None = None, **fields: Any) -> dict[str, Any]:
        with locked(self.lock_path):
            if self.roster_path.exists():
                try:
                    roster = json.loads(self.roster_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise LifecycleError("exec roster unreadable; refusing overwrite: %s" % exc) from exc
            else:
                roster = {"schema": "codex-loop-exec-roster/v2", "jobs": {}}
            jobs = roster.setdefault("jobs", {})
            item = jobs.setdefault(packet_id, {"packet_id": packet_id, "history": []})
            if run_id and item.get("run_id") != run_id:
                current_run = item.get("run_id")
                incoming_start = float(fields.get("started_at", 0) or 0)
                current_start = float(item.get("started_at", 0) or 0)
                # A late heartbeat/completion from an older supervisor must
                # never take ownership back from a newer generation.  Only a
                # fresh ``starting`` record with a non-older start time may
                # advance the packet generation.
                if (current_run and
                        not (fields.get("state") == "starting"
                             and incoming_start >= current_start)):
                    return dict(item)
                item.setdefault("history", []).append({"ts": time.time(),
                    "state": "generation_started", "run_id": run_id,
                    "attempt": attempt})
                # Terminal diagnostics describe exactly one generation.  A
                # retry must retain them only in history/events, never on the
                # live current-generation row where observers could mistake
                # stale failure evidence for the new run's outcome.
                for key in ("exit_code", "failure", "stop_reason",
                            "published_report", "cleanup_status",
                            "cleanup_failed_at"):
                    item.pop(key, None)
                item["run_id"] = run_id
                item["attempt"] = attempt
            state = fields.get("state")
            if state and state != item.get("state"):
                history_row = {"ts": time.time(), "state": state,
                    "run_id": run_id or item.get("run_id"),
                    "attempt": attempt if attempt is not None else item.get("attempt")}
                # Preserve immutable terminal evidence by run generation.
                # The packet-keyed live row may be overwritten by a retry
                # before a parent wait samples it; retaining these bounded
                # fields in history prevents that ABA from losing completion.
                if state in TERMINAL_STATES:
                    for key in ("exit_code", "failure", "stop_reason",
                                "published_report"):
                        if key in fields:
                            history_row[key] = fields[key]
                item.setdefault("history", []).append(history_row)
            item.update(fields)
            item["updated_at"] = time.time()
            atomic_json(self.roster_path, roster)
            return dict(item)

    def event_exists(self, packet_id: str, run_id: str, event: str) -> bool:
        try:
            with self.events_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if (row.get("packet_id") == packet_id and row.get("run_id") == run_id
                            and row.get("event") == event):
                        return True
        except OSError:
            pass
        return False

    def append_event_once(self, packet_id: str, run_id: str, attempt: int,
                          event: str, detail: dict[str, Any]) -> bool:
        event_lock = self.root / ".events.lock"
        with locked(event_lock):
            if self.event_exists(packet_id, run_id, event):
                return False
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            row = {"ts": time.time(), "packet_id": packet_id, "run_id": run_id,
                   "attempt": attempt, "event": event, "detail": detail}
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            return True

    def cancel_request(self, packet_id: str, run_id: str, attempt: int) -> dict[str, Any] | None:
        path = self.cancel_dir / (packet_id + ".json")
        row = load_json(path, {})
        if (isinstance(row, dict) and row and row.get("run_id") == run_id
                and int(row.get("attempt", -1)) == attempt):
            return row
        return None

    def consume_cancel(self, packet_id: str, run_id: str, attempt: int) -> None:
        path = self.cancel_dir / (packet_id + ".json")
        with locked(self.lock_path):
            row = load_json(path, {})
            if (isinstance(row, dict) and row.get("run_id") == run_id
                    and int(row.get("attempt", -1)) == attempt):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


class PosixBoundary:
    def __init__(self, argv: Sequence[str], cwd: Path, stdout_path: Path, stderr_path: Path):
        self.stdout = stdout_path.open("ab", buffering=0)
        self.stderr = stderr_path.open("ab", buffering=0)
        self.proc = subprocess.Popen(list(argv), cwd=str(cwd), stdout=self.stdout,
                                     stderr=self.stderr, start_new_session=True)
        self.pid = self.proc.pid

    def poll(self) -> int | None:
        return self.proc.poll()

    def group_exists(self) -> bool:
        try:
            os.killpg(self.pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def terminate_tree(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

    def kill_tree(self) -> None:
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def close(self) -> None:
        # A worker may exit while a detached-in-the-same-group grandchild is
        # still alive.  The group, not the leader's poll result, is the unit
        # owned by this supervisor.
        if self.group_exists():
            self.kill_tree()
            deadline = time.monotonic() + 1.0
            while self.group_exists() and time.monotonic() < deadline:
                time.sleep(0.02)
        self.stdout.close()
        self.stderr.close()


if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CREATE_SUSPENDED = 0x00000004
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    CREATE_NO_WINDOW = 0x08000000
    STARTF_USESTDHANDLES = 0x00000100
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    STILL_ACTIVE = 259

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                    ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                    ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
                    ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
                    ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    for fn, args, restype in (
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("WaitForSingleObject", [wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        ("GetExitCodeProcess", [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ("TerminateProcess", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ("ResumeThread", [wintypes.HANDLE], wintypes.DWORD)):
        func = getattr(kernel32, fn); func.argtypes = args; func.restype = restype

    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.CreateProcessW.restype = wintypes.BOOL

    def _win_error(action: str) -> LifecycleError:
        code = ctypes.get_last_error()
        return LifecycleError("%s failed (%d): %s" % (action, code, ctypes.FormatError(code)))

    def _env_block(env: dict[str, str]):
        values = ["%s=%s" % (key, env[key]) for key in sorted(env, key=str.casefold)]
        return ctypes.create_unicode_buffer("\0".join(values) + "\0\0")

    class WindowsBoundary:
        """Race-free CREATE_SUSPENDED -> assign Job -> ResumeThread boundary."""
        def __init__(self, argv: Sequence[str], cwd: Path, stdout_path: Path, stderr_path: Path,
                     *, _kernel32=None, _win_error_fn=None):
            import msvcrt
            self.api = _kernel32 or kernel32
            self.win_error = _win_error_fn or _win_error
            self.stdout = stdout_path.open("ab", buffering=0)
            self.stderr = stderr_path.open("ab", buffering=0)
            self.stdin = open(os.devnull, "rb", buffering=0)
            handles = [msvcrt.get_osfhandle(x.fileno()) for x in (self.stdin, self.stdout, self.stderr)]
            previous = [os.get_handle_inheritable(h) for h in handles]
            self.job = self.api.CreateJobObjectW(None, None)
            if not self.job:
                error = self.win_error("CreateJobObjectW")
                self.close()
                raise error
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
                                                        JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
            if not self.api.SetInformationJobObject(self.job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                     ctypes.byref(limits), ctypes.sizeof(limits)):
                error = self.win_error("SetInformationJobObject")
                self.close()
                raise error
            startup = STARTUPINFOW(); startup.cb = ctypes.sizeof(startup)
            startup.dwFlags = STARTF_USESTDHANDLES
            startup.hStdInput, startup.hStdOutput, startup.hStdError = handles
            info = PROCESS_INFORMATION()
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
            environment = _env_block(dict(os.environ))
            try:
                try:
                    for h in handles: os.set_handle_inheritable(h, True)
                    ok = self.api.CreateProcessW(None, command, None, None, True,
                        CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
                        ctypes.cast(environment, ctypes.c_void_p), str(cwd),
                        ctypes.byref(startup), ctypes.byref(info))
                finally:
                    for h, value in zip(handles, previous): os.set_handle_inheritable(h, value)
            except BaseException:
                self.close()
                raise
            if not ok:
                error = self.win_error("CreateProcessW")
                self.close(); raise error
            self.process = info.hProcess; self.thread = info.hThread
            self.pid = int(info.dwProcessId)
            try:
                if not self.api.AssignProcessToJobObject(self.job, self.process):
                    raise self.win_error("AssignProcessToJobObject")
                if self.api.ResumeThread(self.thread) == 0xFFFFFFFF:
                    raise self.win_error("ResumeThread")
            except BaseException as exc:
                # Assignment can fail before the suspended process belongs to
                # our Job (for example under an incompatible outer Job).
                # TerminateProcess is the strong-handle fallback; terminating
                # our empty Job alone would leave a permanent suspended orphan.
                terminate_error = None
                if self.process and not self.api.TerminateProcess(self.process, 0xC000013A):
                    terminate_error = self.win_error("TerminateProcess")
                self.close()
                if terminate_error:
                    raise LifecycleError("%s; suspended-child cleanup also failed: %s" %
                                         (exc, terminate_error)) from exc
                raise
            finally:
                if self.thread:
                    self.api.CloseHandle(self.thread); self.thread = None

        def poll(self) -> int | None:
            status = self.api.WaitForSingleObject(self.process, 0)
            if status == WAIT_TIMEOUT: return None
            if status != WAIT_OBJECT_0: raise _win_error("WaitForSingleObject")
            code = wintypes.DWORD()
            if not self.api.GetExitCodeProcess(self.process, ctypes.byref(code)):
                raise self.win_error("GetExitCodeProcess")
            return int(code.value)

        def terminate_tree(self) -> None:
            if self.poll() is None and not self.api.TerminateJobObject(self.job, 0xC000013A):
                raise self.win_error("TerminateJobObject")

        kill_tree = terminate_tree

        def close(self) -> None:
            for name in ("process", "thread", "job"):
                handle = getattr(self, name, None)
                if handle:
                    self.api.CloseHandle(handle); setattr(self, name, None)
            for name in ("stdin", "stdout", "stderr"):
                stream = getattr(self, name, None)
                if stream:
                    stream.close(); setattr(self, name, None)


def spawn_boundary(argv: Sequence[str], cwd: Path, stdout_path: Path, stderr_path: Path):
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        return WindowsBoundary(argv, cwd, stdout_path, stderr_path)
    return PosixBoundary(argv, cwd, stdout_path, stderr_path)


def stderr_tail(path: Path, limit: int = 8192) -> str:
    try:
        raw = path.read_bytes()[-limit:]
        return raw.decode("utf-8", errors="replace")
    except OSError:
        return ""


def schedule_epilogue(root: Path, source: str) -> dict[str, Any]:
    """Durably request the zero-model terminal transaction.

    Coalescing never discards an edge: every caller increments requested_seq.
    The active runner drains until consumed_seq catches up.
    """
    log_path = root / "data" / "orchestration" / "terminal_epilogue.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    marker = root / "data" / "orchestration" / "terminal_epilogue.pid"
    requests = root / "data" / "orchestration" / "terminal_requests.json"
    claim = root / "data" / "orchestration" / ".terminal_epilogue.lock"
    with locked(claim):
        request_state = {"requested_seq": 0, "consumed_seq": 0}
        try:
            loaded = json.loads(requests.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                request_state.update(loaded)
        except (OSError, ValueError):
            pass
        request_state["requested_seq"] = int(
            request_state.get("requested_seq", 0) or 0) + 1
        request_state.update(source=source, updated_at=time.time())
        atomic_json(requests, request_state)
        prior_pid = None
        prior_ticks = None
        try:
            marker_value = json.loads(marker.read_text(encoding="utf-8"))
            prior_pid = int(marker_value["pid"])
            prior_ticks = int(marker_value["proc_start_ticks"])
        except (OSError, ValueError):
            pass
        except (KeyError, TypeError):
            prior_pid = None
        if prior_pid:
            if runner_generation_alive(prior_pid, prior_ticks):
                return {"status": "coalesced", "pid": prior_pid, "source": source}
        log = log_path.open("ab")
        kwargs: dict[str, Any] = {
            "cwd": str(root), "env": {**os.environ, "LOOP_ROOT": str(root)},
            "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT,
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                       | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [sys.executable, str(root / "harness" / "terminal_packet_epilogue.py")],
                **kwargs)
            atomic_json(marker, {"pid": proc.pid,
                                 "proc_start_ticks": proc_start_ticks(proc.pid)})
            return {"status": "scheduled", "pid": proc.pid, "source": source}
        finally:
            log.close()


def runner_generation_alive(pid: int, expected_ticks: int | None) -> bool:
    """Return true only for the exact live process generation in the marker."""
    if expected_ticks is None:
        return False
    try:
        if os.name == "nt":
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            alive = False
            if handle:
                try:
                    code = ctypes.c_ulong()
                    alive = bool(ctypes.windll.kernel32.GetExitCodeProcess(
                        handle, ctypes.byref(code))) and code.value == 259
                finally:
                    ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(int(pid), 0)
            alive = True
    except (OSError, TypeError, ValueError):
        return False
    return alive and proc_start_ticks(int(pid)) == int(expected_ticks)


def proc_start_ticks(pid: int) -> int | None:
    """Cross-platform process creation token used to reject PID reuse."""
    if os.name == "nt":
        query = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
        handle = ctypes.windll.kernel32.OpenProcess(query, False, int(pid))
        if not handle:
            return None
        try:
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if not ctypes.windll.kernel32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_time),
                    ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        fields = Path("/proc/%d/stat" % pid).read_text(encoding="ascii").split()
        return int(fields[21])
    except (OSError, ValueError, IndexError):
        return None


def publish_report(source: Path, destination: Path, cwd: Path, data_dir: Path,
                   packet_id: str | None = None,
                   run_id: str | None = None) -> None:
    source = source.resolve()
    destination = destination.resolve()
    cwd = cwd.resolve()
    reports_root = (data_dir / "reports").resolve()
    try:
        source.relative_to(cwd)
        destination.relative_to(reports_root)
    except ValueError as exc:
        raise LifecycleError("report copy path escaped owned roots") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(destination.name + ".%d.tmp" % os.getpid())
    raw = source.read_bytes()
    if packet_id:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            value = None
        if not isinstance(value, dict) or value.get("packet_id") != packet_id:
            value = {"packet_id": packet_id, "status": "done",
                     "run_id": run_id,
                     "summary": raw.decode("utf-8", errors="replace")}
        raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tmp.open("wb") as dst:
        dst.write(raw)
        dst.flush()
        os.fsync(dst.fileno())
    os.replace(tmp, destination)


def run(args: argparse.Namespace) -> int:
    data_dir = args.data_dir.resolve()
    store = Store(data_dir)
    provider_root = data_dir.parent
    started = time.time()
    monotonic_started = time.monotonic()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        raise LifecycleError("missing child command after --")
    base = {"state": "starting", "task_name": args.task_name,
            "role": args.role, "model": args.model,
            "plane": args.plane or ("Windows CLI" if os.name == "nt" else "WSL CLI"),
            "cwd": str(args.cwd.resolve()), "supervisor_pid": os.getpid(),
            "supervisor_proc_start_ticks": proc_start_ticks(os.getpid()),
            "parent_session_id": args.parent_session_id,
            "manifest_id": getattr(args, "manifest_id", None),
            "started_at": started, "deadline_at": started + args.timeout,
            "command": command, "stdout_path": str(args.stdout),
            "stderr_path": str(args.stderr)}
    store.update(args.packet, run_id=args.run_id, attempt=args.attempt, **base)
    boundary = None
    terminal = False
    try:
        # Every lifecycle-supervised execution role is a leaf. The inherited
        # marker lets the global PreToolUse hook deny recursive agent births
        # even though a standalone codex-exec rollout has no Desktop parent id.
        old_leaf = os.environ.get("LOOP_LEAF_AGENT")
        os.environ["LOOP_LEAF_AGENT"] = "1"
        try:
            boundary = spawn_boundary(command, args.cwd.resolve(), args.stdout, args.stderr)
        finally:
            if old_leaf is None:
                os.environ.pop("LOOP_LEAF_AGENT", None)
            else:
                os.environ["LOOP_LEAF_AGENT"] = old_leaf
        store.update(args.packet, run_id=args.run_id, attempt=args.attempt,
                     state="running", os_pid=boundary.pid,
                     worker_proc_start_ticks=proc_start_ticks(boundary.pid), heartbeat_at=time.time())
        store.append_event_once(args.packet, args.run_id, args.attempt, "exec_spawned",
                                {"os_pid": boundary.pid, "task_name": args.task_name})
        reason = None
        heartbeat_at = time.monotonic()
        while True:
            rc = boundary.poll()
            if rc is not None:
                break
            cancel = store.cancel_request(args.packet, args.run_id, args.attempt)
            reason = str(cancel.get("reason") or "parent_stop") if cancel else None
            if reason or time.monotonic() > monotonic_started + args.timeout:
                reason = reason or "timeout"
                boundary.terminate_tree()
                deadline = time.monotonic() + args.grace
                group_alive = getattr(boundary, "group_exists", lambda: boundary.poll() is None)
                while group_alive() and time.monotonic() < deadline:
                    time.sleep(POLL_SECONDS)
                if group_alive():
                    boundary.kill_tree()
                    kill_deadline = time.monotonic() + max(1.0, args.grace)
                    while group_alive() and time.monotonic() < kill_deadline:
                        time.sleep(0.02)
                rc = boundary.poll()
                state = "cancelled" if reason != "timeout" else "timed_out"
                store.update(args.packet, run_id=args.run_id, attempt=args.attempt,
                             state=state, exit_code=rc, stop_reason=reason)
                event = "timeout" if reason == "timeout" else "exec_failed"
                store.append_event_once(args.packet, args.run_id, args.attempt, event,
                    {"why": reason, "cancelled": reason != "timeout",
                     "os_pid": boundary.pid, "limit_s": args.timeout,
                     "grace_s": args.grace})
                try:
                    from provider_health import record_failure
                    record_failure(provider_root, args.model, run_id=args.run_id,
                                   rc=124, stderr=stderr_tail(args.stderr),
                                   events=stderr_tail(args.stdout), timed_out=reason == "timeout")
                except Exception as exc:
                    print("provider health recording degraded: %s" % exc, file=sys.stderr)
                if cancel:
                    store.consume_cancel(args.packet, args.run_id, args.attempt)
                terminal = True
                return 124
            if time.monotonic() - heartbeat_at >= HEARTBEAT_INTERVAL_S:
                store.update(args.packet, run_id=args.run_id, attempt=args.attempt,
                             state="running", heartbeat_at=time.time())
                heartbeat_at = time.monotonic()
            time.sleep(POLL_SECONDS)

        if rc == 0 and args.report.exists():
            if args.publish_report:
                publish_report(args.report, args.publish_report, args.cwd, data_dir,
                               None if args.l2_idem_key else args.packet,
                               args.run_id)
            published = args.publish_report or args.report
            if args.l2_idem_key:
                try:
                    from l2_consumer import L2Consumer
                    validation = L2Consumer(data_dir.parent).complete(
                        args.l2_idem_key, published,
                        expected_revision=args.l2_revision)
                except Exception as exc:
                    detail = {"why": "l2_completion_failed",
                              "error": "%s: %s" % (type(exc).__name__, exc),
                              "report": str(published)}
                    store.update(args.packet, run_id=args.run_id,
                                 attempt=args.attempt, state="failed",
                                 exit_code=1, failure=detail)
                    store.append_event_once(args.packet, args.run_id,
                                            args.attempt, "exec_failed", detail)
                    return 1
                if not validation.ok:
                    detail = {"why": "l2_report_invalid",
                              "validation": validation.to_dict(),
                              "report": str(published)}
                    store.update(args.packet, run_id=args.run_id,
                                 attempt=args.attempt, state="failed",
                                 exit_code=1, failure=detail)
                    store.append_event_once(args.packet, args.run_id,
                                            args.attempt, "exec_failed", detail)
                    return 1
            store.update(args.packet, run_id=args.run_id, attempt=args.attempt,
                         state="completed", exit_code=0,
                         published_report=str(args.publish_report) if args.publish_report else str(args.report))
            store.append_event_once(args.packet, args.run_id, args.attempt, "subagent_stop",
                                    {"source": "lifecycle_supervisor", "exit_code": 0})
            try:
                from provider_health import record_success
                record_success(provider_root, args.model, run_id=args.run_id)
            except Exception as exc:
                print("provider health recording degraded: %s" % exc, file=sys.stderr)
            terminal = True
            return 0
        why = "missing_report" if rc == 0 else "nonzero_exit"
        detail = {"why": why, "exit_code": rc, "stderr_path": str(args.stderr),
                  "stderr_tail": stderr_tail(args.stderr)}
        store.update(args.packet, run_id=args.run_id, attempt=args.attempt,
                     state="failed", exit_code=rc, failure=detail)
        store.append_event_once(args.packet, args.run_id, args.attempt, "exec_failed", detail)
        try:
            from provider_health import record_failure
            record_failure(provider_root, args.model, run_id=args.run_id,
                           rc=int(rc or 1), stderr=detail["stderr_tail"],
                           events=stderr_tail(args.stdout), timed_out=False)
        except Exception as exc:
            print("provider health recording degraded: %s" % exc, file=sys.stderr)
        terminal = True
        return int(rc or 1)
    except BaseException as exc:
        # Only a failure before a child boundary exists is a spawn failure.
        # Once the child reached the process boundary, a later heartbeat or
        # publication failure must not rewrite history as "never born".
        phase = "pre_spawn" if boundary is None else "post_spawn"
        state = "spawn_failed" if boundary is None else "failed"
        detail = {"why": "supervisor_error", "phase": phase,
                  "error": "%s: %s" % (type(exc).__name__, exc)}
        try:
            store.update(args.packet, run_id=args.run_id, attempt=args.attempt,
                         state=state, failure=detail)
        except BaseException as record_exc:
            # Do not replace the original supervisor failure with a second
            # roster-write traceback.  The cold-start reconciler uses the
            # supervisor PID/create-time token to turn any stale running row
            # into a visible ``supervisor_lost`` failure on restart.
            print("lifecycle supervisor could not record %s failure: %s" %
                  (phase, record_exc), file=sys.stderr)
        try:
            store.append_event_once(args.packet, args.run_id, args.attempt,
                                    "exec_failed", detail)
        except BaseException as event_exc:
            print("lifecycle supervisor could not append failure event: %s" %
                  event_exc, file=sys.stderr)
        terminal = boundary is not None
        return 1
    finally:
        if boundary is not None:
            boundary.close()
        if terminal:
            try:
                root = data_dir.parent
                # Generic adhoc headless waves share the lifecycle roster but
                # are not canonical state-machine packets.  Their terminal
                # edge may request refill, but must not drain the canonical
                # event stream through the full epilogue.
                packet_file = root / "data" / "packets" / (args.packet + ".json")
                if packet_file.is_file():
                    schedule_epilogue(root, "lifecycle_supervisor")
                else:
                    sys.path.insert(0, str(root / "harness"))
                    from refill_consumer_v2 import schedule_run
                    schedule_run(root, source="adhoc_lifecycle_supervisor")
            except Exception as exc:
                print("terminal epilogue scheduling degraded: %s" % exc,
                      file=sys.stderr)
        # A terminal supervisor owns the matching v2 budget reservation.
        # Reclaim is idempotent and best-effort; lifecycle truth must never be
        # rewritten if budget telemetry is unavailable.
        try:
            sys.path.insert(0, str(data_dir.parent / "harness"))
            from budget_controller import BudgetController
            from orchestration_common import LoopPaths
            BudgetController(LoopPaths.resolve(data_dir.parent)).reclaim(args.run_id)
        except Exception as exc:
            print("budget reclaim degraded for %s: %s" %
                  (args.run_id, exc), file=sys.stderr)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Codex LOOP zero-model worker lifecycle supervisor")
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--packet", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--attempt", type=int, required=True)
    ap.add_argument("--parent-session-id")
    ap.add_argument("--manifest-id")
    ap.add_argument("--task-name", required=True)
    ap.add_argument("--role", default="worker")
    ap.add_argument("--model", required=True)
    ap.add_argument("--plane")
    ap.add_argument("--cwd", type=Path, required=True)
    ap.add_argument("--stdout", type=Path, required=True)
    ap.add_argument("--stderr", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--publish-report", type=Path)
    ap.add_argument("--timeout", type=float, required=True)
    ap.add_argument("--grace", type=float, default=10.0)
    ap.add_argument("--l2-idem-key")
    ap.add_argument("--l2-revision", type=int)
    ap.add_argument("command", nargs=argparse.REMAINDER)
    return ap


if __name__ == "__main__":
    raise SystemExit(run(parser().parse_args()))
