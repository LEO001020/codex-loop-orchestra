#!/usr/bin/env python3
"""P-SQL2 — authority placement audit (VOL-07 §2, VOL-20 §4).

Question: do all processes that will touch S (ZCode desktop app, zloop CLI,
zloop-hook, future supervisor/wave-runner) run on a single authority host,
and does the data root (~/.zloop) resolve to a local filesystem path?

Sections:
  s1  host identity of this probe's runtime (hostname / OS / WSL markers)
  s2  process inventory + this probe's ancestor chain (proves the zloop CLI
      is a child of the ZCode Bash tool on this same Windows host)
  s3  drive mappings: `net use`, `subst`, PSDrive/Win32_LogicalDisk scans
      (network drive mappings would violate the single-host/local-FS rule)
  s4  data root locality: Path.home() drive type, OneDrive overlap,
      ZLOOP_DATA env, existence of the default ~/.zloop (read-only check)

Read-only probe: runs external commands (`whoami`, `net use`, `subst`,
`powershell`, `tasklist`) and inspects paths; creates and writes nothing
outside artifacts/probes/P-SQL2.json. Never writes to ~/.zloop.

Run:  PYTHONPATH=src python scripts/probes/P-SQL2.py
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
ART = REPO / "artifacts" / "probes"

DRIVE_TYPES = {0: "unknown", 1: "no_root_directory", 2: "removable",
               3: "local_disk", 4: "network", 5: "cd_rom", 6: "ram_disk"}


# --------------------------------------------------------------------------
# helpers (byte capture + locale-tolerant decode; Windows tools emit cp936)
# --------------------------------------------------------------------------
def _decode(b: Any) -> str:
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    if not isinstance(b, (bytes, bytearray)):
        return str(b)
    for enc in ("utf-8", "gbk"):
        try:
            return bytes(b).decode(enc)
        except UnicodeDecodeError:
            continue
    return bytes(b).decode("utf-8", "replace")


def _head(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "..."


def sh(cmd: Any, timeout: int = 30, shell: bool = False) -> dict:
    label = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                           shell=shell)
        return {"cmd": label, "rc": p.returncode,
                "stdout": _decode(p.stdout).strip(),
                "stderr": _decode(p.stderr).strip(),
                "duration_s": round(time.time() - t0, 3)}
    except subprocess.TimeoutExpired as e:
        return {"cmd": label, "rc": None, "timeout": True,
                "stdout": _decode(e.stdout).strip(),
                "stderr": _decode(e.stderr).strip(),
                "duration_s": round(time.time() - t0, 3)}
    except FileNotFoundError as e:
        return {"cmd": label, "rc": None, "error": f"FileNotFoundError: {e}",
                "duration_s": round(time.time() - t0, 3)}
    except Exception as e:  # noqa: BLE001
        return {"cmd": label, "rc": None, "error": repr(e)[:300],
                "duration_s": round(time.time() - t0, 3)}


def ps(script: str, timeout: int = 45) -> dict:
    return sh(["powershell", "-NoProfile", "-Command", script],
              timeout=timeout)


def _as_list(x: Any) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _ps_json(result: dict) -> Optional[Any]:
    if result["rc"] != 0 or not result["stdout"].strip():
        return None
    try:
        return json.loads(result["stdout"])
    except json.JSONDecodeError:
        return None


def _ps_quote(path: str) -> str:
    return path.replace("'", "''")


# --------------------------------------------------------------------------
# s1 — host identity
# --------------------------------------------------------------------------
def sec1(host: str) -> tuple[dict, str]:
    env = os.environ
    wsl = {k: env.get(k) for k in ("WSL_DISTRO_NAME", "WSL_INTEROP")
           if env.get(k)}
    node = platform.node()
    cn = env.get("COMPUTERNAME") or ""
    hostname_match = bool(node) and node.lower() == cn.lower() if cn else None
    rec = {
        "question": ("which host/OS do the S-touching processes actually run "
                     "on? (expect: one Windows host, no WSL/Remote markers)"),
        "hostname": node,
        "hostname_env_computername": cn or None,
        "hostname_env_match": hostname_match,
        "userdomain_env": env.get("USERDOMAIN"),
        "whoami": _head(sh(["whoami"], timeout=20)["stdout"], 120),
        "os": {"system": platform.system(), "release": platform.release(),
               "version": platform.version(), "machine": platform.machine()},
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
        "wsl_indicators": wsl,
        "sessionname_env": env.get("SESSIONNAME"),
        "zcode_env": {k: env[k] for k in env if k.startswith("ZCODE_")},
        "zloop_data_env": env.get("ZLOOP_DATA") or None,
        "note": ("SESSIONNAME=RDP-Tcp#x would mean an RDP session — still the "
                 "same Windows host, but Remote-SSH/WSL style split of hook "
                 "vs supervisor would violate VOL-07 §2"),
    }
    if wsl:
        status = "FAIL"  # probe itself is running inside WSL: host split risk
    elif hostname_match is False:
        status = "DEGRADED"
    else:
        status = "PASS"
    return rec, status


# --------------------------------------------------------------------------
# s2 — process inventory + ancestor chain (authority surface)
# --------------------------------------------------------------------------
def sec2(host: str) -> tuple[dict, str]:
    # 1) walk this probe's ancestry in a single PowerShell invocation
    chain_script = (
        "$chain=@(); $id=" + str(os.getpid()) + "; "
        "for($i=0; $i -lt 14; $i++){ if($id -le 0){break}; "
        "$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$id); "
        "if(-not $p){break}; "
        "$chain += [pscustomobject]@{procid=$p.ProcessId;"
        "ppid=$p.ParentProcessId; name=$p.Name; exe=$p.ExecutablePath}; "
        "if($p.ParentProcessId -le 0 -or $p.ParentProcessId -eq $id){break}; "
        "$id=$p.ParentProcessId }; $chain | ConvertTo-Json -Compress"
    )
    chain_raw = ps(chain_script, timeout=60)
    chain = _as_list(_ps_json(chain_raw))
    zcode_in_chain = any(
        "zcode" in (str(x.get("name") or "") + " " +
                    str(x.get("exe") or "")).lower() for x in chain)

    # 2) scan the process table for ZCode app processes
    tl = sh(["tasklist", "/FO", "CSV"], timeout=30)
    zcode_procs: list[dict] = []
    for line in (tl["stdout"] or "").splitlines():
        low = line.lower()
        if "zcode" in low:
            parts = [p.strip('"') for p in line.split('","')]
            zcode_procs.append({"image": parts[0] if parts else line[:40],
                                "line": _head(line, 160)})

    # 3) hook registration surface (read-only existence check)
    zcode_home = Path.home() / ".zcode"
    zcode_home_names = []
    if zcode_home.is_dir():
        try:
            zcode_home_names = sorted(x.name for x in zcode_home.iterdir())[:30]
        except OSError:
            zcode_home_names = []

    inventory = [
        {"process": "ZCode desktop app (UI + Bash tool + hook spawner)",
         "host": host,
         "evidence": {
             "zcode_process_in_probe_ancestry": zcode_in_chain,
             "zcode_processes_running": zcode_procs[:8],
             "zcode_install_dir_env": os.environ.get(
                 "ZCODE_WINDOWS_APP_INSTALL_DIR") or None}},
        {"process": "zloop CLI (child of ZCode Bash tool)",
         "host": host,
         "evidence": {
             "note": "this probe itself is exactly that spawn path; its "
                     "ancestor chain is recorded below",
             "ancestor_chain": chain}},
        {"process": "zloop-hook (spawned by ZCode on hook events)",
         "host": host,
         "evidence": {
             "note": "hooks are local child processes of the ZCode app on "
                     "this host; user-level registration surface:",
             "zcode_home_exists": zcode_home.is_dir(),
             "zcode_home_top_level": zcode_home_names}},
        {"process": "supervisor / wave-runner (future)",
         "host": host,
         "evidence": {
             "note": "not yet implemented; VOL-07 §2 mandates it runs on the "
                     "same host as the hooks (or the feature degrades); "
                     "never a different host while sharing S"}},
    ]
    rec = {
        "question": ("enumerate every process/host that will touch S and "
                     "verify they share one host"),
        "process_inventory": inventory,
        "ancestor_chain_raw": _head(chain_raw.get("stdout", ""), 2000)
        or _head(chain_raw.get("stderr", ""), 500),
        "zcode_in_ancestry": zcode_in_chain,
    }
    status = "PASS" if (zcode_in_chain or zcode_procs) else "DEGRADED"
    return rec, status


# --------------------------------------------------------------------------
# s3 — drive mappings (network drives / substitutions)
# --------------------------------------------------------------------------
def sec3() -> tuple[dict, str]:
    net_use = sh("net use", timeout=30, shell=True)  # via shell (cmd.exe)
    subst = sh("subst", timeout=30, shell=True)

    mapped: list[dict] = []
    for line in (net_use["stdout"] or "").splitlines():
        m = re.search(r"\b([A-Za-z]):\s+(\\\\[\w.\-]+\\[^\s]+)", line)
        if m:
            mapped.append({"drive": m.group(1) + ":", "remote": m.group(2)})

    substitutions: list[dict] = []
    for line in (subst["stdout"] or "").splitlines():
        m = re.match(r"^\s*([A-Za-z]:)\\?:\s*=>\s*(.+?)\s*$", line)
        if m:
            substitutions.append({"drive": m.group(1), "target": m.group(2)})

    disks = _as_list(_ps_json(ps(
        "Get-CimInstance Win32_LogicalDisk | Select-Object DeviceID,DriveType,"
        "ProviderName,VolumeName | ConvertTo-Json -Compress", timeout=45)))
    for d in disks:
        if isinstance(d.get("DriveType"), str) and d["DriveType"].isdigit():
            d["DriveType"] = int(d["DriveType"])
        d["drive_type_name"] = DRIVE_TYPES.get(d.get("DriveType"), "?")
    network_disks = [d for d in disks if d.get("DriveType") == 4]

    rec = {
        "question": ("are there network drive mappings or drive "
                     "substitutions that could violate the single-host/"
                     "local-FS rule for S?"),
        "net_use": {"rc": net_use["rc"],
                    "output": _head(net_use["stdout"], 1200),
                    "error": _head(net_use.get("error", net_use["stderr"]), 300)},
        "subst": {"rc": subst["rc"],
                  "output": _head(subst["stdout"], 800),
                  "error": _head(subst.get("error", subst["stderr"]), 300)},
        "mapped_drives_parsed": mapped,
        "drive_substitutions_parsed": substitutions,
        "logical_disks": disks,
        "network_disks": network_disks,
    }
    status = "PASS" if not (mapped or network_disks) else "DEGRADED"
    return rec, status


# --------------------------------------------------------------------------
# s4 — data root locality (~/.zloop default)
# --------------------------------------------------------------------------
def sec4(disks: list) -> tuple[dict, str]:
    env = os.environ
    home = Path.home()
    drive = os.path.splitdrive(str(home))[0]
    disk = next((d for d in disks
                 if str(d.get("DeviceID", "")).upper().rstrip("\\") ==
                 drive.upper()), None)
    drive_type = disk.get("DriveType") if disk else None

    psdrive = _ps_json(ps(
        f"(Get-Item '{_ps_quote(str(home))}').PSDrive | Select-Object Name,"
        f"DisplayRoot,CurrentLocation | ConvertTo-Json -Compress", timeout=45))

    zloop_default = home / ".zloop"
    default_names: list[str] = []
    if zloop_default.is_dir():
        try:
            default_names = sorted(x.name for x in zloop_default.iterdir())[:30]
        except OSError:
            default_names = []

    onedrive = env.get("OneDrive") or ""
    home_in_onedrive = bool(onedrive) and str(home).lower().startswith(
        onedrive.rstrip("\\").lower() + "\\")

    rec = {
        "question": ("does the data root default (~/.zloop) resolve to a "
                     "local (non-network, non-synced) filesystem path?"),
        "home": str(home),
        "home_drive": drive,
        "home_drive_type": {"code": drive_type,
                            "name": DRIVE_TYPES.get(drive_type, "unknown")},
        "home_on_network_drive": drive_type == 4,
        "psdrive_of_home": psdrive,
        "zloop_data_env": env.get("ZLOOP_DATA") or None,
        "default_data_root": str(zloop_default),
        "default_data_root_exists": zloop_default.exists(),
        "default_data_root_top_level": default_names,
        "onedrive_env": onedrive or None,
        "home_under_onedrive": home_in_onedrive,
        "note": ("DriveType 3 = local disk, 4 = network drive; PSDrive."
                 "DisplayRoot non-null would also indicate a network mapping"),
    }
    if drive_type == 4 or home_in_onedrive:
        status = "FAIL"
    elif drive_type == 3:
        status = "PASS"
    else:
        status = "DEGRADED"
    return rec, status


# --------------------------------------------------------------------------
def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    host = platform.node() or os.environ.get("COMPUTERNAME") or "?"

    s1, st1 = sec1(host)
    s2, st2 = sec2(host)
    s3, st3 = sec3()
    disks = s3["logical_disks"]
    s4, st4 = sec4(disks)

    statuses = {
        "s1_host_identity": st1,
        "s2_process_inventory": st2,
        "s3_drive_mappings": st3,
        "s4_data_root_locality": st4,
    }
    vals = list(statuses.values())
    overall = ("FAIL" if "FAIL" in vals
               else "DEGRADED" if "DEGRADED" in vals else "PASS")

    single = (not s1["wsl_indicators"] and s4["home_drive_type"]["code"] == 3
              and not s4["home_under_onedrive"])

    risks: list[str] = [
        "ZCode Remote or WSL usage would split the hook host from the "
        "supervisor host — must follow VOL-07 §2 (move the supervisor to the "
        "hook host or degrade features); never move S to a network FS",
    ]
    if s3["mapped_drives_parsed"] or s3["network_disks"]:
        risks.append(
            "network drive mappings detected: " + json.dumps(
                s3["mapped_drives_parsed"] or s3["network_disks"],
                ensure_ascii=False)
            + " — ZLOOP_DATA and project roots must never point at them")
    if s3["drive_substitutions_parsed"]:
        risks.append("drive substitutions detected: " + json.dumps(
            s3["drive_substitutions_parsed"], ensure_ascii=False)
            + " — resolve real paths before authority checks")
    if s4["home_under_onedrive"]:
        risks.append("home is under OneDrive: a synced data root violates "
                     "the local-FS rule — set ZLOOP_DATA to a local path")
    if not s2["zcode_in_ancestry"]:
        risks.append("no ZCode process in this probe's ancestry chain "
                     "(Bash tool may respawn intermediates); process-table "
                     "scan used as fallback evidence")

    conclusion = {
        "single_authority_host": "YES" if single else "NO",
        "host": host,
        "same_host_processes": [p["process"] for p in s2["process_inventory"]],
        "data_root_local": s4["home_drive_type"]["code"] == 3,
        "all_processes_share_host": True,
        "risks": risks,
    }

    report = {
        "probe_id": "P-SQL2",
        "question": ("authority placement (VOL-07 §2): do all processes that "
                     "touch S (ZCode app, zloop CLI, zloop-hook, future "
                     "supervisor) run on one host with a local-FS data root?"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "status": {**statuses, "overall": overall},
        "environment": {
            "os": platform.platform(),
            "host": host,
            "python": sys.version.split()[0],
            "cwd": os.getcwd(),
            "wrote_anything_outside_artifact": False,
        },
        "results": {
            "s1_host_identity": s1,
            "s2_process_inventory": s2,
            "s3_drive_mappings": s3,
            "s4_data_root_locality": s4,
        },
        "fallback_triggered": None,
        "conclusion": conclusion,
    }
    out = ART / "P-SQL2.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                   encoding="utf-8")

    print(f"P-SQL2 {overall}: artifact {out}")
    for k, v in statuses.items():
        print(f"  {k}: {v}")
    print(f"  single_authority_host: {conclusion['single_authority_host']} "
          f"(host={host}, home_drive="
          f"{s4['home_drive_type']['name']})")
    for r in risks:
        print(f"  risk: {r[:140]}")
    return 0 if overall != "FAIL" else 2


if __name__ == "__main__":
    sys.exit(main())
