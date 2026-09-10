#!/usr/bin/env python3
# ============================================================================
# dag_assert.py — DAG assertion (spec §3.3 transition 2, PLANNED->DISPATCHABLE)
# Purpose : Assert dag.json is acyclic AND intra-wave authorized_paths are
#           pairwise non-intersecting (write-parallel physical isolation).
# Usage   : dag_assert.py [DAG_JSON] | -h. Default DAG_JSON is
#           $LOOP_ROOT/data/packets/dag.json; packets always load from there.
# Input   : data/packets/dag.json {"edges":[[a,b],...],"waves":[[pid,...],...]}
#           + data/packets/<pid>.json (4-field packets with authorized_paths).
# Output  : exit 0 = pass, 1 = fail (reasons on stderr), 2 = usage/bad input
#           (never a traceback). Zero-token, no LLM.
# Lines   : ~65 (excluding this header)
# ============================================================================
import json, os, sys
ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PK = os.path.join(ROOT, "data", "packets")
USAGE = """dag_assert.py — assert dag.json is acyclic and each wave's authorized_paths are disjoint.
usage: dag_assert.py [DAG_JSON]   (default: $LOOP_ROOT/data/packets/dag.json)
       packets are read from $LOOP_ROOT/data/packets/<packet_id>.json
exit:  0 = pass, 1 = assertion failed (reasons on stderr), 2 = usage/bad input"""

def fail(msg):
    sys.stderr.write("DAG_ASSERT FAIL: %s\n" % msg); sys.exit(1)

def bad_input(msg):
    sys.stderr.write("DAG_ASSERT: %s\n" % msg); sys.exit(2)

def load(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        bad_input("%s not found (%s)" % (path, what))
    except ValueError as exc:
        bad_input("%s is not valid JSON (%s)" % (path, exc))
    except OSError as exc:
        bad_input("%s unreadable (%s)" % (path, exc))

argv = sys.argv[1:]
if argv and argv[0] in ("-h", "--help"):
    print(USAGE); sys.exit(0)
if len(argv) > 1:
    bad_input("too many arguments; usage: dag_assert.py [DAG_JSON]")
dag_path = argv[0] if argv else os.path.join(PK, "dag.json")
dag = load(dag_path, "explicit argument" if argv else "default; pass a path or set LOOP_ROOT")
if not isinstance(dag, dict):
    bad_input("%s must hold a JSON object with edges/waves" % dag_path)
edges, waves = dag.get("edges", []), dag.get("waves", [])
nodes = {n for e in edges for n in e} | {p for w in waves for p in w}
# Kahn's algorithm: acyclic iff all nodes can be topologically removed.
indeg = {n: 0 for n in nodes}
for a, b in edges:
    indeg[b] += 1
queue, seen = [n for n in nodes if indeg[n] == 0], 0
while queue:
    n = queue.pop(); seen += 1
    for a, b in edges:
        if a == n:
            indeg[b] -= 1
            if indeg[b] == 0:
                queue.append(b)
if seen != len(nodes):
    fail("cycle detected (%d/%d nodes sorted)" % (seen, len(nodes)))
norm = lambda p: p.rstrip("/") + "/"
for w, wave in enumerate(waves):
    paths = {}
    for pid in wave:
        packet = load(os.path.join(PK, pid + ".json"), "packet in wave %d" % w)
        if not isinstance(packet, dict) or "authorized_paths" not in packet:
            bad_input("%s.json has no authorized_paths" % pid)
        paths[pid] = packet["authorized_paths"]
    for i, a in enumerate(wave):
        for b in wave[i + 1:]:
            for pa in paths[a]:
                for pb in paths[b]:
                    if norm(pa).startswith(norm(pb)) or norm(pb).startswith(norm(pa)):
                        fail("wave %d: %s and %s intersect on %r vs %r" % (w, a, b, pa, pb))
print("DAG_ASSERT PASS: %d nodes acyclic, %d waves path-disjoint" % (len(nodes), len(waves)))
