# prompt-lab — ZCode root prompt performance overlay

Doctrine (D-22a + D-22b, fourth round, 2026-09-03):

- **D-22a** The root system prompt is an owned, first-class **experimental**
  harness surface. Stock-vs-candidate must be decided by paired evaluation on
  real workloads before production freeze — vendor default is not exempt from
  audit, and neither is any hand-written candidate.
- **D-22b** Root prompt customization is **never a ZLoop correctness
  dependency**. Patch failure / unknown hash / failed verification => stock
  ZCode runs; evidence, research, C2C, supervisor, promotion all keep working.
  No ZLoop module may import or require this lab.

Hard rules:

1. Version-pinned: only the exact sha256 in `known-builds.json` is patched.
   After any ZCode upgrade the hash changes and every patch REFUSES — that is
   the intended fail-soft behavior. Never freeze ZCode updates to keep a patch.
2. Preimage-anchored: every patch matches one exact unique string (count==1
   asserted) — never byte ranges, never offsets.
3. Guarded pipeline: backup pristine bundle -> patch to temp -> `node --check`
   -> atomic replace. Any failure refuses before touching the live file.
4. One-shot experiments: apply, verify in a FRESH session, restore, verify the
   restore in another fresh session.

## P-SENT1 protocol (run this first, before any real candidate)

```
python patch.py status                      # expect: STOCK (known build)
python patch.py apply candidates/sentinel.json
#  -> open a FRESH ZCode root session
#     send exactly:  ZCODE_PROMPT_PROBE_7F31
#     expect exactly: PROMPT_PATCH_ACTIVE
#     run one normal Read and one Bash to confirm nothing else broke
python patch.py restore
#  -> open another FRESH session: ZCODE_PROMPT_PROBE_7F31 must now behave
#     as a normal unknown phrase (probe gone)
```

Only after P-SENT1 passes does the candidate ladder begin:
communication-block-only surgery (P4 first knife) -> full Cluster B ->
2x2 paired eval (Stock/P4 x Full/Pruned tools) -> context-pressure axis
(32K/64K/128K/256K) -> clause-level ablation with return experiments
(A0..A6 per v3-CANDIDATES §12). Metrics: mechanical success, T_trusted,
then tokens/calls/TTFT plus diagnostic variables (wrong-tool selection,
permission retry loops, premature completion, constraint-caused failures).
Statistics: task-blocked randomization in time, paired bootstrap /
Wilcoxon; pre-register the primary endpoint before running.

## Files

- `patch.py` — status / apply / restore (see module docstring)
- `known-builds.json` — the only patchable stock sha256(s)
- `candidates/sentinel.json` — P-SENT1 probe
- `candidates/p4-clusterB.md` — GPT fourth-round Cluster-B candidate (design;
  needs mode-matrix capture before a patch candidate can be derived)
