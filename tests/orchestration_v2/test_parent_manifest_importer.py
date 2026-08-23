from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.orchestration_v2.conftest import make_root
from orchestration_common import LoopPaths, read_json
from parent_manifest_importer import (ParentManifestError, import_manifest,
                                       packet_id_for, validate_manifest)


@pytest.fixture(autouse=True)
def no_real_refill_actuator(monkeypatch):
    import refill_consumer_v2
    monkeypatch.setattr(refill_consumer_v2, "schedule_run",
                        lambda root, *, source: {"status": "scheduled",
                                                "source": source})


def parent_doc(root: Path, *, task_id: str = "audit-1", role: str = "worker") -> dict:
    return {
        "schema": "codex-loop-parent-refill/v1",
        "manifest_id": "manifest-test-1",
        "parent_session_id": "019ff196-c08c-76a2-8c1f-64b7b4a093ef",
        "parent_state": "active",
        "enabled": True,
        "target_active": 20,
        "cwd": str(root),
        "mode": "read-only",
        "tasks": [{
            "task_id": task_id, "task_name": "具体只读审计任务",
            "goal": "检查一个明确的只读边界",
            "authorized_paths": ["src/"],
            "acceptance": ["返回证据路径"], "role": role,
            "sandbox": "read-only", "cwd": str(root),
        }],
    }


def write_manifest(tmp_path: Path, doc: dict) -> Path:
    path = tmp_path / "parent.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_import_is_explicit_idempotent_and_dispatchable(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    manifest = write_manifest(tmp_path, parent_doc(root))
    wakes = []
    import refill_consumer_v2
    monkeypatch.setattr(refill_consumer_v2, "schedule_run",
                        lambda root, *, source: wakes.append((root, source)) or
                        {"status": "scheduled"})

    first = import_manifest(root, manifest)
    second = import_manifest(root, manifest)
    pid = packet_id_for("manifest-test-1", "audit-1")
    paths = LoopPaths.resolve(root)
    packet = read_json(paths.data / "packets" / f"{pid}.json")
    ledger = read_json(paths.ledger)

    assert first["imported"] == 1 and second["existing"] == 1
    assert wakes == [(root.resolve(), "parent_manifest_imported")] * 2
    assert ledger["packets"][pid]["state"] == "DISPATCHABLE"
    assert ledger["packets"][pid]["history"] == []
    assert ledger["packets"][pid]["attempts"] == 0
    assert ledger["packets"][pid]["parent_session_id"] == \
        "019ff196-c08c-76a2-8c1f-64b7b4a093ef"
    assert packet["created_by"] == "parent_manifest_importer"
    assert packet["sandbox"] == "read-only"
    parent = read_json(paths.refill_dir / "parent_sessions.json")["parents"][
        "019ff196-c08c-76a2-8c1f-64b7b4a093ef"]
    assert parent["target_active"] == 20
    assert parent["mode"] == "read-only"
    assert parent["cwd"] == str(root.resolve())
    events = [json.loads(line) for line in paths.events.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == [
        "parent_manifest_imported", "parent_manifest_imported"]


@pytest.mark.parametrize("field,value", [
    ("enabled", False), ("mode", "workspace-write"),
    ("parent_state", "completed"), ("target_active", 21),
])
def test_import_rejects_inactive_or_unsafe_parent(tmp_path, field, value):
    root = make_root(tmp_path)
    doc = parent_doc(root)
    doc[field] = value
    with pytest.raises(ParentManifestError):
        validate_manifest(doc, root)


def test_import_separates_dialogue_target_from_bounded_backlog(tmp_path):
    root = make_root(tmp_path)
    doc = parent_doc(root)
    doc["target_active"] = 20
    assert validate_manifest(doc, root)["target_active"] == 20
    doc["tasks"] = [
        {**doc["tasks"][0], "task_id": f"audit-{index}"}
        for index in range(40)
    ]
    assert len(validate_manifest(doc, root)["tasks"]) == 40
    doc["tasks"] = [
        {**doc["tasks"][0], "task_id": f"audit-{index}"}
        for index in range(81)
    ]
    with pytest.raises(ParentManifestError, match="at most 80"):
        validate_manifest(doc, root)


def test_import_rejects_roleless_write_and_unsafe_cwd(tmp_path):
    root = make_root(tmp_path)
    doc = parent_doc(root)
    doc["tasks"][0]["role"] = ""
    with pytest.raises(ParentManifestError, match="role"):
        validate_manifest(doc, root)
    doc = parent_doc(root)
    doc["tasks"][0]["sandbox"] = "workspace-write"
    with pytest.raises(ParentManifestError, match="read-only"):
        validate_manifest(doc, root)
    doc = parent_doc(root)
    doc["cwd"] = str(tmp_path.parent)
    with pytest.raises(ParentManifestError, match="outside"):
        validate_manifest(doc, root)


def test_parent_cwd_requires_explicit_extra_root(tmp_path):
    root = make_root(tmp_path)
    vps = tmp_path / "vps"
    vps.mkdir()
    doc = parent_doc(root)
    doc["cwd"] = str(vps)
    with pytest.raises(ParentManifestError, match="outside"):
        validate_manifest(doc, root)
    assert validate_manifest(doc, root, [vps])["cwd"] == str(vps.resolve())


def test_parent_cwd_rejects_traversal_before_resolution(tmp_path):
    root = make_root(tmp_path)
    doc = parent_doc(root)
    doc["cwd"] = str(root / ".." / root.name)
    with pytest.raises(ParentManifestError, match="traversal"):
        validate_manifest(doc, root)


def test_import_rejects_collision_without_overwrite(tmp_path):
    root = make_root(tmp_path)
    manifest = write_manifest(tmp_path, parent_doc(root))
    import_manifest(root, manifest)
    paths = LoopPaths.resolve(root)
    pid = packet_id_for("manifest-test-1", "audit-1")
    packet_path = paths.data / "packets" / f"{pid}.json"
    packet = read_json(packet_path)
    packet["goal"] = "tampered"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    with pytest.raises(ParentManifestError, match="collision"):
        import_manifest(root, manifest)


def test_batch_collision_is_detected_before_any_new_packet_write(tmp_path):
    root = make_root(tmp_path)
    doc = parent_doc(root, task_id="first")
    second = dict(doc["tasks"][0])
    second["task_id"] = "second"
    doc["tasks"].append(second)
    manifest = write_manifest(tmp_path, doc)
    paths = LoopPaths.resolve(root)
    packet_dir = paths.data / "packets"
    packet_dir.mkdir(parents=True, exist_ok=True)
    second_pid = packet_id_for("manifest-test-1", "second")
    (packet_dir / f"{second_pid}.json").write_text(
        json.dumps({"packet_id": second_pid, "goal": "collision"}), encoding="utf-8")

    with pytest.raises(ParentManifestError, match="collision"):
        import_manifest(root, manifest)

    first_pid = packet_id_for("manifest-test-1", "first")
    assert not (packet_dir / f"{first_pid}.json").exists()
    assert first_pid not in (read_json(paths.ledger, {"packets": {}}).get("packets") or {})


def test_exact_one_sided_crash_state_is_repaired(tmp_path):
    root = make_root(tmp_path)
    manifest = write_manifest(tmp_path, parent_doc(root))
    first = import_manifest(root, manifest)
    paths = LoopPaths.resolve(root)
    pid = first["packet_ids"][0]
    ledger = read_json(paths.ledger)
    ledger["packets"].pop(pid)
    paths.ledger.write_text(json.dumps(ledger), encoding="utf-8")

    repaired = import_manifest(root, manifest)

    assert repaired["imported"] == 1
    assert read_json(paths.ledger)["packets"][pid]["state"] == "DISPATCHABLE"


def test_reimport_accepts_normal_lifecycle_evolution(tmp_path):
    root = make_root(tmp_path)
    manifest = write_manifest(tmp_path, parent_doc(root))
    first = import_manifest(root, manifest)
    paths = LoopPaths.resolve(root)
    pid = first["packet_ids"][0]
    ledger = read_json(paths.ledger)
    ledger["packets"][pid].update({
        "state": "RUNNING", "attempts": 1, "current_run_id": "run-1",
        "history": [{"to": "RUNNING", "via": "dispatched"}],
    })
    paths.ledger.write_text(json.dumps(ledger), encoding="utf-8")
    again = import_manifest(root, manifest)
    assert again["existing"] == 1 and again["imported"] == 0
    assert read_json(paths.ledger)["packets"][pid]["state"] == "RUNNING"


def test_stopped_parent_cannot_be_reactivated_by_a_new_manifest(tmp_path):
    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    paths.refill_dir.mkdir(parents=True, exist_ok=True)
    parent_id = parent_doc(root)["parent_session_id"]
    (paths.refill_dir / "parent_sessions.json").write_text(json.dumps({
        "parents": {parent_id: {
            "active": False, "manifest_id": "older-manifest",
            "source": "parent_stop",
        }}
    }), encoding="utf-8")
    doc = parent_doc(root)
    doc["manifest_id"] = "newer-manifest"

    with pytest.raises(ParentManifestError, match="cannot be implicitly reactivated"):
        import_manifest(root, write_manifest(tmp_path, doc))

    assert not (paths.data / "packets").exists()


def test_parent_stop_wins_when_it_races_the_final_registration(
        tmp_path, monkeypatch):
    import parent_manifest_importer as importer

    root = make_root(tmp_path)
    paths = LoopPaths.resolve(root)
    doc = parent_doc(root)
    parent_id = doc["parent_session_id"]
    manifest = write_manifest(tmp_path, doc)
    original_atomic_write = importer.atomic_write_json
    stop_injected = False

    def inject_stop_after_ledger(path, value):
        nonlocal stop_injected
        original_atomic_write(path, value)
        if Path(path) == paths.ledger and not stop_injected:
            stop_injected = True
            original_atomic_write(paths.refill_dir / "parent_sessions.json", {
                "parents": {parent_id: {
                    "active": False, "manifest_id": doc["manifest_id"],
                    "source": "parent_stop",
                }}
            })

    monkeypatch.setattr(importer, "atomic_write_json", inject_stop_after_ledger)

    with pytest.raises(ParentManifestError, match="stopped while manifest was importing"):
        import_manifest(root, manifest)

    parent = read_json(paths.refill_dir / "parent_sessions.json")["parents"][parent_id]
    assert parent["active"] is False
    pid = packet_id_for(doc["manifest_id"], doc["tasks"][0]["task_id"])
    assert read_json(paths.ledger)["packets"][pid]["state"] == "DISPATCHABLE"
