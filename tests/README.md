# Operator validation scripts

Device crawl scripts are intentionally left for operator execution from the existing SIKSIK dashboard. Contract and build scripts do not interact with a connected phone.

Instagram/X source contract, Android APK assembly, backend syntax, and frontend production build:

```sh
./tests/run_social_crawl_checks.sh
```

Backend contract suite:

```sh
./tests/run_contract_tests.sh
```

ADB command construction and two-screen permission UX contract, using a fake transport only:

```sh
python3 tests/android_adb_automation_contract.py
```

SQLite concurrency and incremental live-ingestion telemetry, using temporary databases only:

```sh
backend/.venv/bin/python tests/sqlite_concurrency_check.py
backend/.venv/bin/python tests/live_ingestion_progress_check.py
```

End-to-end dashboard session ledger:

```sh
python3 tests/dashboard_social_flow_check.py \
  --db backend/data/poc.db \
  --session-id <session_id> \
  --require-screenshot \
  --require-profile-data \
  --expected-scope own_posts \
  --expected-scope own_story_archive \
  --expected-scope own_comments
```

For X, use `own_tweets` and `own_replies`. The script opens SQLite read-only and emits only state, counts, and scope names.

Full-scan capability and phase benchmark, using a baseline measured on the same phone and dataset:

```sh
python3 tests/full_scan_benchmark.py \
  --db backend/data/poc.db \
  --session-id <session_id> \
  --min-images 1 \
  --min-pdfs 1 \
  --min-sms 1 \
  --min-instagram-posts 1 \
  --min-instagram-stories 1 \
  --min-x-posts 1 \
  --baseline-ms <comparison_total_ms> \
  --require-faster
```

The benchmark reads SQLite in read-only mode and reports only states, counts, and timings. A faster-than-Cellebrite claim is valid only when the baseline uses the same device, dataset, extraction scope, and start/stop boundaries.
