# Baseline v2 Summary

Generated (UTC): 2026-02-23T01:32:29.180038Z

## Suite Status

| Suite | Status | Exit | Note |
|---|---|---:|---|
| ci_benchmark | passed | 0 | pass_rate=1.000 |
| redteam_suite | passed | 0 | failed=0 / tasks=11 |
| aesa_suite | passed | 0 | pass_rate=0.700 |
| coding_suite | passed | 0 | pass_rate=1.000 |
| kafka_smoke | failed | 1 | docker daemon unavailable (dockerDesktopLinuxEngine pipe not found) |

## Metrics Snapshot

- pass_rate.ci_benchmark: 1.000
- pass_rate.aesa_suite: 0.700
- pass_rate.coding_suite: 1.000
- latency.ci_p50_s: 0.033
- latency.ci_p95_s: 0.180
- repair_effectiveness.lift: 0.000

## Top Failures

- AESA: lint_failed (count=1, share=0.333)
- AESA: scope_violation (count=1, share=0.333)
- AESA: tests_failed (count=1, share=0.333)
- CI benchmark: none
- Redteam: none

## Freeze Notes

- Baseline artifacts are frozen under `artifacts/baseline_v2/`.
- Kafka smoke must be rerun once Docker daemon is available to reach fully-green baseline.
