# CHAT-01 evaluation assets

This directory contains the versioned contracts and evaluation runner for Qin
Weixi free-dialogue evaluation. It does not contain training data.

Current checkpoint: `CHAT-01` complete through `CHAT-01G`.

- `schema/` contains JSON Schema 2020-12 contracts.
- `examples/` contains synthetic records used only to test schema parsing.
- `suites/` contains the frozen suite manifest, canon snapshot, drift register,
  80-case development suite, 240-case frozen single-turn suite, 30-scenario
  frozen multi-turn suite, 68-unit human suite and leakage report.
- `build_suites.py` defines the deterministic CHAT-01C source builder. It now
  refuses to overwrite frozen v1 assets; changes require a new suite version.
- `rules.py` executes deterministic checks and creates pending semantic-review
  records without calling a model or silently finalizing semantic blockers.
- `rubrics/blocker_rules_v1.json` is the executable rule registry, and
  `rubrics/semantic_rubric_v1.md` defines mandatory human blocker review.
- `rubrics/quality_rubric_v1.json` defines six fully anchored quality
  dimensions; `rubrics/human_blind_rubric_v1.md` defines blind review policy.
- `suites/chat01_human_blind_v1.jsonl` contains 60 pairwise units and eight
  30-60 minute long-session briefs without oracle answers.
- `blind.py` creates committed random A/B assignments and only reveals model
  mappings after a ballot has been submitted.
- `leakage_guard.py` compares all player-visible evaluation text with effective
  Qin Weixi training inputs using exact, normalized and character-trigram checks.
- `freeze.py` verifies canonical inputs, leakage evidence, the manifest self-hash,
  and all 27 frozen file byte counts and SHA256 values.
- `runner.py` executes the reporting pipeline. Its `CHAT-01G` mode uses only
  scripted Fake outputs and records the runner hash in each run manifest.
- `reports/` contains immutable per-run output directories and never overwrites
  the frozen suite inputs.

Verify the immutable contract with:

```powershell
.\.venv\Scripts\python.exe -m eval.chat01.freeze --verify
```

The training-side hash-only blocklist is
`training_package_m3_v2/eval_exclusions/chat01_v1.json`. The M3_v2 packager
rejects a missing or empty blocklist and drops an entire conversation when any
human turn matches a frozen evaluation text. This freeze does not mean that a
model passed. `CHAT-01G` proved only that reporting handles pass, fail, blocker,
timeout and invalid samples. Real candidate comparison belongs to `CHAT-02`.

Run the Fake-only tooling self-test with:

```powershell
.\.venv\Scripts\python.exe -m eval.chat01.runner
```
