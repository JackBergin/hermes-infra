# Behavioral regression evaluations

**Infrastructure health is not agent quality.** A container may stay up while a model change, prompt edit, skill conflict, context compression or tool change harms output quality.

This repository includes an explicitly opt-in *basic output-contract harness*. It does not score open-ended reasoning quality or guarantee model repeatability. Use it as the seed of a larger, domain-specific benchmark.

## Run

After `make setup`, `make configure`, `make up`:

```bash
make eval
# Or:
python3 scripts/evaluate.py --cases evals/cases.json --min-pass-rate 0.95
```

**Warning:** Running tests makes real provider calls, incurs cost, creates Hermes sessions, and may change memory/skills. It runs against the default agent profile. Use a **separate test deployment**, not production, when data isolation matters. CI deliberately runs only pure evaluation code unit tests, never real agent turns.

The harness records pass/fail, duration, exit code, output hash, and current Git revision under ignored `reports/`. Raw model outputs are excluded by default. Supply `--include-output` only on a trusted machine when debugging, because the transcript may contain secrets or private user data.

## Improve it

Replace the three toy cases in `evals/cases.json` with high-value examples drawn from your actual workload. Cases currently support `expected_exact`, `expected_contains` (list), or `expected_json`.

Version the evaluation dataset in Git and compare results across releases. For meaningful quality regression coverage, add domain-specific assertions, tool-call traces, baseline comparisons, independent judge models, human review for subjective tasks, and a repeat-run policy for stochastic output. Pin evaluation input, environment, model selection and skill revision in release manifests.

The default assertions are intentionally simple, not an enterprise evaluation platform.
