# Task 3 Report: Structured Reports and Safe HTML

## Delivered

- Added `symphonz.service.reporting` with an allowlisted report contract,
  immutable document model, bounded strings and collections, active issue
  identity checks, and HTTP(S)-only URLs.
- Reports render as deterministic escaped HTML/CSS, including architecture,
  validation, review metadata, sticky navigation, collapsible decisions, and
  print styles. Agent-supplied markup is never treated as HTML.
- Publication writes versioned report generations through fsynced temporary
  files and records a stable issue report URL. The authenticated handler for
  that URL is explicitly deferred to Task 4.
- RuntimeStore records report state before Linear synchronization. The
  runtime-owned `## Symphonz Implementation Report` comment is created once,
  updated on subsequent publication, and failed synchronization stays pending
  with bounded exponential retry state and an error record.
- Added dynamic-tool specifications and explicit routing for both
  `linear_graphql` and `symphonz_report`.

## TDD Evidence

1. Added `tests/test_symphonz_reporting.py` before production code.
2. Ran `python3 -m unittest tests.test_symphonz_reporting -v` and observed all
   six tests fail with `ModuleNotFoundError` for the missing reporting module.
3. Implemented the minimum module and routing needed for the contract.
4. Re-ran focused, service, bytecode, diff, and full-suite verification.

## Verification

- `python3 -m unittest tests.test_symphonz_reporting -v`: 6 passed.
- `python3 -m unittest tests.test_symphonz_service -v`: 76 passed.
- `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-pycache python3 -m py_compile symphonz/service/reporting.py symphonz/service/dynamic_tools.py`: passed.
- `git diff --check`: passed.
- `python3 -m unittest discover -v`: 149 passed on the final serial run.

## Follow-up Risk

`CodexAppServerTests.test_never_approval_policy_rejects_unexpected_approval_request`
is timing-sensitive. During this re-review it passed in isolation but failed
in two serial full-suite runs because the fake app server did not record the
decline response before process cleanup. This task does not modify the
app-server path.

## Review Fixes 3-11

- Report rows now contain bounded index metadata only. Initial and retry
  synchronization reload and validate the full document from the authoritative
  JSON artifact. Missing and corrupt artifacts remain pending, emit report-sync
  errors, and advance exponential backoff.
- Artifact roots and issue directories are opened as non-symlink directory file
  descriptors with inode checks. Directory creation, temporary writes, rename,
  cleanup, and fsync use relative `dir_fd` operations; root replacement and
  issue-directory symlinks fail closed.
- RuntimeStore now provides an issue-scoped transactional report-sync lease.
  Comment discovery paginates, recovers create-before-state-save by querying
  Linear again, and only accepts mutation responses with `success is True` and
  a non-empty comment ID. Sync-state writes are fenced by the expected JSON
  path so an older worker cannot replace a newer authoritative bundle.
- Publications use paired `report-<generation>.json` and
  `report-<generation>.html` files. RuntimeStore paths switch only after both
  files and the issue directory are fsynced. A second-write failure preserves
  the previous paths and files; successful replacement removes the superseded
  generation. Task 4 will serve the RuntimeStore-authoritative HTML generation
  through safe artifact access; Task 3 does not implement an HTTP route.
- `symphonz_report` now advertises a recursive strict schema with publish const,
  required fields, exact object properties, and runtime-aligned string and
  collection limits.
- Linear comment fields collapse line breaks and neutralize mentions, headings,
  links, backticks, and code-span closure. Branch and commit remain bounded safe
  code text.
- Report-sync failures write both RuntimeStore and the dedicated error sink.
  A later success resolves all prior unresolved report-sync database errors for
  the issue.
- `public_base_url` rejects credentials, missing or malformed hosts,
  unsupported schemes, query, fragment, control characters, and traversal
  segments. Repeated path separators normalize before the stable issue route is
  appended.

## Review TDD Evidence

1. Added RED coverage for every required finding before its corresponding
   implementation. The initial focused run reported 12 failures and 5 errors,
   including missing lease/error-sink APIs, metadata-truncated restart retry,
   fixed filenames, unpinned symlinks, unpaginated comments, unchecked business
   success, permissive schema/Markdown/base URL handling, and non-atomic bundle
   replacement.
2. Added a separate RED fencing test after review of the concurrent publish and
   sync data flow; it failed because `update_report_sync_state` did not exist.
3. Implemented each storage and publication boundary, then reran focused tests
   after each storage, reporting, and resource-lifetime change.

## Review Verification

- `python3 -m unittest tests.test_symphonz_reporting tests.test_symphonz_service -v`:
  96 passed.
- `python3 -m unittest discover -v`: 163 passed on the final serial run.
- `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-pycache python3 -m py_compile symphonz/*.py symphonz/service/*.py tests/*.py`:
  passed.
- `sh -n install.sh`: passed.
- `git diff --check`: passed.

The known app-server approval timing test failed once during final full-suite
verification, passed immediately in isolation, and passed in the final serial
full-suite retry. No runner, orchestrator, or app-server code changed.

## Deferred Task 5 Integration

- Wire `symphonz_report` advertisement and execution through `CodexAppServer`
  and `runner.py` with a production-shaped test.
- Call the concrete `ReportPublisher.sync_pending()` from `Orchestrator.tick()`,
  prove restart recovery, and remove the process-global weak registries.

## Re-review Fixes 12-17

- Report-sync leases now use unique owners, owner-conditional state updates,
  and an active heartbeat renewal. A worker that observes lease loss stops
  before a Linear create/update or report-state write.
- `ReportPublisher` retains the validated artifact-root directory descriptor
  for its lifetime. RuntimeStore persists relative generation names, and the
  publisher exposes pinned-root `read_current_json()` and `read_current_html()`
  accessors for Task 4.
- After each successful generation commit, the issue directory is scanned and
  every non-authoritative `report-*.json` or `report-*.html` file is removed.
- AI-controlled Linear fields neutralize bare `http://` and `https://` text in
  addition to existing Markdown-significant characters.
- The advertised `review.url` schema now requires an HTTP(S) pattern and
  rejects obvious credential-bearing authorities, matching runtime URL checks.
- The stable report URL remains a Task 4 authenticated-handler requirement;
  this Task 3 implementation publishes and safely reads only the authoritative
  artifact generation.

## Re-review Verification

- RED: `python3 -m unittest tests.test_symphonz_reporting tests.test_symphonz_service -v`
  failed as expected before implementation, covering the missing lease renewal,
  owner fencing, safe readers, relative names, orphan cleanup, URL neutralizing,
  and URL-schema behavior.
- `python3 -m unittest tests.test_symphonz_reporting -v`: 20 passed.
- `python3 -m unittest tests.test_symphonz_service -v`: 80 passed.
- `python3 -m unittest discover -v`: two final serial runs each completed 166
  passing tests and failed only in the known timing-sensitive
  `CodexAppServerTests.test_never_approval_policy_rejects_unexpected_approval_request`.
  Its isolated invocation passed; no forbidden app-server path was changed.
- `env PYTHONPYCACHEPREFIX=/private/tmp/symphonz-pycache python3 -m py_compile
  symphonz/service/reporting.py symphonz/service/runtime_store.py
  tests/test_symphonz_reporting.py tests/test_symphonz_service.py`: passed.
- `git diff --check`: passed.
