#!/usr/bin/env python3
# ============================================================================
# dag_assert.py — DAG assertion (spec §3.3 transition 2, PLANNED->DISPATCHABLE)
# Purpose : Assert dag.json is acyclic AND intra-wave authorized_paths are
#           pairwise non-intersecting (write-parallel physical isolation).
# Input   : data/packets/dag.json {"edges":[[a,b],...],"waves":[[pid,...],...]}
#           + data/packets/<pid>.json (4-field packets with authorized_paths).
# Output  : exit 0 = pass, 1 = fail (reasons on stderr). Zero-token, no LLM.
# Lines   : ~30 (excluding this header)
# ============================================================================
import json, os, sys
ROOT = os.environ.get("LOOP_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PK = os.path.join(ROOT, "data", "packets")

def fail(msg):
    sys.stderr.write("DAG_ASSERT FAIL: %s\n" % msg); sys.exit(1)

dag = json.load(open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(PK, "dag.json")))
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
    paths = {pid: json.load(open(os.path.join(PK, pid + ".json")))["authorized_paths"] for pid in wave}
    for i, a in enumerate(wave):
        for b in wave[i + 1:]:
            for pa in paths[a]:
                for pb in paths[b]:
                    if norm(pa).startswith(norm(pb)) or norm(pb).startswith(norm(pa)):
                        fail("wave %d: %s and %s intersect on %r vs %r" % (w, a, b, pa, pb))
print("DAG_ASSERT PASS: %d nodes acyclic, %d waves path-disjoint" % (len(nodes), len(waves)))
