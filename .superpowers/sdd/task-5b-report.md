# Task 5B: Explicit Pending Report Synchronizer

Status: DONE

Commit: `f814961`

## Changed Files

- `symphonz/service/reporting.py`: removed weak global publisher registries and
  the module-level fallback, added `PendingReportSynchronizer`, and retained
  issue-scoped publisher retry behavior through the existing lease, heartbeat,
  backoff, error, and artifact-validation paths.
- `symphonz/service/runtime_store.py`: added the bounded oldest-first
  `list_due_reports()` query, the `reports_due_sync` index, latest-version
  filtering, and NULL-safe fenced path matching for malformed metadata.
- `tests/test_symphonz_reporting.py`: added due-query, true restart, batch
  bound, malformed metadata, missing artifact, deterministic close, global
  removal, and concurrent lease idempotency coverage.

## RED Evidence

Command:

`python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_due_report_query_is_indexed_bounded_and_oldest_first tests.test_symphonz_reporting.ReportingTests.test_explicit_synchronizer_restarts_without_global_publisher_registry tests.test_symphonz_reporting.ReportingTests.test_explicit_synchronizer_bounds_each_batch_without_starving_old_reports tests.test_symphonz_reporting.ReportingTests.test_explicit_synchronizer_isolates_bad_rows_and_closes_temporary_publishers tests.test_symphonz_reporting.ReportingTests.test_concurrent_explicit_synchronizers_share_sqlite_lease`

Result: expected RED, exit code 1. `RuntimeStore.list_due_reports` and
`PendingReportSynchronizer` were missing, and the weak global registry kept the
closed original publisher alive after deletion and garbage collection.

## GREEN Evidence

- The same five focused Task 5B tests passed: 5 tests in 0.312s.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests
  tests.test_symphonz_service.RuntimeStoreTests`: 65 tests passed in 2.561s.
- `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-task-5b-pycache python3 -m
  py_compile symphonz/service/reporting.py symphonz/service/runtime_store.py
  tests/test_symphonz_reporting.py`: passed.
- `git diff --check`: passed.

## Review Notes

- A relevant legacy-schema test initially exposed index creation before the
  `linear_sync_status` compatibility migration. Moving index creation after the
  migration made the exact reproduction and all nine report-store compatibility
  and lease tests pass.
- SQLite query planning uses `reports_due_sync` for pending rows and the reports
  primary-key covering index for latest-version exclusion.
- The restart test closes and deletes the original publisher, proves it is
  collected, replaces the original store object with a newly opened
  `RuntimeStore`, and synchronizes the persisted pending report explicitly.

## Concerns

None.

This report is ignored and intentionally excluded from the commit.

---

## Review Finding Remediation (2026-07-17)

Status: DONE

Commit message: `fix(reporting): fence pending report synchronization`

### Fixed Findings

- `list_due_reports()` excludes unexpired issue leases before `LIMIT`, using
  wall-clock time for lease expiry and caller-supplied time only for retry due
  eligibility.
- Due selection, claim, heartbeat authority checks, successful sync writes,
  and pending failure writes are fenced by exact `report_version` and selected
  JSON generation. Explicit `NULL` generation paths remain exact values.
- Heartbeat start failures enter the existing pending-error path, stop safely,
  release the lease, close the temporary publisher, and do not abort the batch.
- Unexpected per-row synchronizer exceptions are isolated, while publisher
  close and idempotent lease release are attempted unconditionally.

### Strict TDD RED Evidence

Each regression was run alone before its production change and failed for the
expected missing behavior:

- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_due_report_query_excludes_wall_clock_active_leases_before_limit`
  failed in 0.069s because `SYM-1` was returned instead of unleased `SYM-2`.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_synchronizer_does_not_claim_a_newer_version_after_due_selection`
  failed in 0.064s because the stale selection performed Linear calls.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_synchronizer_does_not_claim_a_new_generation_after_due_selection`
  failed in 0.068s because the replacement generation synchronized.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_malformed_null_generation_failure_updates_only_selected_version`
  failed in 0.044s because both `NULL`-path versions received the retry update.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_newer_version_after_claim_prevents_linear_mutation_and_state_update`
  failed in 0.057s because a Linear create mutation ran after version 2 appeared.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_heartbeat_start_failure_isolated_released_closed_and_recorded`
  failed in 0.075s because the start exception aborted the batch.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests.test_unexpected_claimed_row_exception_is_recorded_released_and_isolated`
  failed in 0.071s because the claimed-row exception aborted the batch.

### GREEN Evidence

- The seven regression tests above passed together: 7 tests in 0.344s.
- `python3 -m unittest tests.test_symphonz_reporting.ReportingTests tests.test_symphonz_service.RuntimeStoreTests`:
  72 tests passed in 2.935s.
- `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-task-5b-review-pycache python3 -m py_compile symphonz/service/reporting.py symphonz/service/runtime_store.py tests/test_symphonz_reporting.py`:
  passed with exit code 0.
- `git diff --check`: passed with exit code 0.

### Self-Review

- Existing direct `RuntimeStore` claim/update and `ReportPublisher` retry APIs
  remain compatible through optional fence parameters; the explicit
  synchronizer always supplies both fences.
- Lease expiry comparisons consistently use wall-clock time in due selection,
  claim, renew, heartbeat ownership, and state persistence.
- Only the four owned Task 5B paths are included in this remediation commit;
  unrelated worktree changes were not modified or staged.

### Concerns

None.

This remediation appendix is intentionally included in the fix commit.
