# ============================================================================
# test_summary_synth.py — Unit tests for harness/summary_synth.py
# Cases: small wave renders fully within budget (normal), oversized wave
#        degrades but stays within budget while keeping pointer integrity
#        (boundary), conflict pointers never dropped, dead-letter duty
#        attribution present, --out file written, malformed input (rc 2).
# ============================================================================
import json
import math
import subprocess

from tests.conftest import PY, HARNESS


def synth(tmp_path, wave, budget=500, out=None):
    f = tmp_path / "wave.json"
    f.write_text(json.dumps(wave))
    cmd = [PY, str(HARNESS / "summary_synth.py"), "--wave", str(f),
           "--budget-tokens", str(budget)]
    if out:
        cmd += ["--out", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def pkt(pid, state="MERGED", note="ok", read=False):
    return {"packet_id": pid, "state": state,
            "report_path": "data/reports/%s/report.json" % pid,
            "note": note, "read": read}


def tokens(text):
    return math.ceil(len(text) / 4)


def test_small_wave_fully_rendered(tmp_path):
    wave = {"wave_id": 1, "packets": [pkt("p1", read=True), pkt("p2")],
            "dead_letters": [], "conflicts": []}
    p = synth(tmp_path, wave)
    assert p.returncode == 0
    assert "WAVE 1 FINALE — 2 packets, 0 dead-letter, 0 conflicts" in p.stdout
    assert "[READ] p1 data/reports/p1/report.json" in p.stdout
    assert "[UNREAD] p2 data/reports/p2/report.json" in p.stdout
    assert tokens(p.stdout) <= 500


def test_oversized_wave_degrades_within_budget(tmp_path):
    wave = {"wave_id": 2,
            "packets": [pkt("p%03d" % i, note="long prose " * 40)
                        for i in range(60)],
            "dead_letters": [], "conflicts": []}
    p = synth(tmp_path, wave, budget=500)
    assert p.returncode == 0
    assert tokens(p.stdout) <= 500              # budget enforced
    # pointer integrity: capped rows collapse to an index pointer, not silence
    assert "data/reports/INDEX.json" in p.stdout
    assert "+"  in p.stdout                     # "+N more" tail present


def test_conflict_pointers_never_dropped(tmp_path):
    wave = {"wave_id": 3,
            "packets": [pkt("p%03d" % i, note="x" * 200) for i in range(80)],
            "dead_letters": [],
            "conflicts": [{"packet_id": "pC",
                           "pointer": "worktrees/pC/REBASE_HEAD"}]}
    p = synth(tmp_path, wave, budget=300)       # tight budget forces max degrade
    assert p.returncode == 0
    assert tokens(p.stdout) <= 300
    assert "pC -> worktrees/pC/REBASE_HEAD" in p.stdout


def test_dead_letter_rows_carry_duty_attribution(tmp_path):
    wave = {"wave_id": 4, "packets": [pkt("p1")],
            "dead_letters": [{"packet_id": "pX",
                              "report_path": "data/dead_letters/pX.json",
                              "duty_attribution": "terminal:report.md:41",
                              "note": "regex_no_match"}],
            "conflicts": []}
    p = synth(tmp_path, wave)
    assert p.returncode == 0
    assert "DEAD LETTERS (duty officer attribution)" in p.stdout
    assert "pX [terminal:report.md:41]" in p.stdout
    assert "data/dead_letters/pX.json" in p.stdout


def test_out_file_written(tmp_path):
    out = tmp_path / "summary.txt"
    wave = {"wave_id": 5, "packets": [pkt("p1")], "dead_letters": [],
            "conflicts": []}
    p = synth(tmp_path, wave, out=out)
    assert p.returncode == 0
    assert out.read_text(encoding="utf-8") == p.stdout


def test_malformed_wave_json_is_usage_error(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{broken")
    p = subprocess.run([PY, str(HARNESS / "summary_synth.py"), "--wave", str(f)],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode == 2
