# Task 6: Installation, Migration, Workflow, and Symphonz 0.4.0

Status: DONE

## Scope

Implemented the 0.4.0 installation and migration surface for the authenticated
dashboard and task reports. The change covers initial installation,
`configure-dashboard`, configured runtime propagation, legacy configuration
compatibility, generated workflow publication rules, release documentation,
and version reporting.

## Implementation

- Added dashboard host, port, public base URL, username, and session duration
  fields to `InstallConfig`. `write_config` emits them as strings in a
  `[dashboard]` TOML section.
- Extended interactive installation with dashboard prompts and an injected,
  testable `getpass` boundary. Passwords never pass through `input`.
- Extended `--yes` installation with all six `SYMPHONZ_DASHBOARD_*`
  environment variables and made `SYMPHONZ_DASHBOARD_PASSWORD` mandatory.
- Initial installation now writes the existing atomic private `auth.toml`,
  creates `.symphonz/artifacts`, and ignores artifacts, logs, workspace, and
  auth material.
- Added public `configure_dashboard(...)` and the `symphonz
  configure-dashboard` command. It resolves a password from the explicit API,
  environment, or `getpass`, replaces only the `[dashboard]` section, rotates
  `auth.toml`, and updates `.gitignore`.
- Dashboard migration preserves all TOML content outside `[dashboard]` and
  does not call Git, Linear preflight, or workflow generation.
- `symphonz run` now loads configured dashboard values and supports temporary
  `--host` and `--port` overrides. The internal `service` parser exposes host,
  port, public base URL, dashboard username, and session duration and forwards
  all values to `run_service`.
- Legacy configs without `[dashboard]` retain the original no-dashboard
  command shape and exact explicit `--port` behavior. Their effective host
  remains the runner's loopback default.
- Updated the repository workflow used by `render_workflow`: Codex must create
  the review request first, call `symphonz_report`, require a report URL,
  confirm both report and review URLs are synchronized to Linear, and only
  then move to `Human Review`. Missing, failed, or pending publication remains
  in `Ready to Publish`.
- Updated README installation, migration, LAN HTTP boundary, overrides,
  authentication/session behavior, and report-link documentation.
- Set `symphonz.__version__` to `0.4.0`.

## Security and Compatibility

- Plaintext dashboard passwords are not written to config, workflow, logs, or
  documentation. Auth material continues to use the existing atomic `0600`
  writer and versioned password hashing.
- Interactive installation always obtains the password through `getpass`,
  even if a password environment variable is present. Environment password
  intake is limited to non-interactive install and dashboard migration.
- `configure-dashboard` does not regenerate project-specific workflow content
  or modify `[linear]`, `[git]`, and other configuration sections.
- Positive integer validation rejects explicit zero for dashboard port and
  session duration instead of silently applying defaults.
- Configs lacking `[dashboard]` do not acquire public URL or non-loopback
  service arguments. Temporary host/port values never rewrite configuration.

## TDD Evidence

The initial focused RED run failed to import `configure_dashboard`, confirming
the migration API did not exist. After the first implementation pass, focused
tests exposed only the intentionally pending workflow, documentation, and
version behavior. A later self-review added three regression contracts and
observed all expected failures before correction:

1. Explicit numeric zero was silently replaced by defaults.
2. A legacy explicit-port command gained new internal flags.
3. Two old provider fallback rules still allowed `Human Review` without a
   report.

Each regression passed after its minimal implementation or workflow change.

## Test Coverage

Added or expanded coverage for:

- Interactive dashboard answers and secure `getpass` injection.
- Non-interactive environment values and required password failure.
- String TOML serialization and no plaintext password persistence.
- Initial artifacts/auth layout and complete `.gitignore` entries.
- Existing and missing `[dashboard]` migration with byte preservation outside
  the section.
- Migration password environment/getpass behavior and no Git, Linear, or
  workflow side effects.
- Public API and CLI dispatch for `configure-dashboard`.
- Configured runtime propagation, temporary overrides, internal service
  dispatch, invalid numeric values, and legacy explicit-port compatibility.
- Report publication ordering, publication blocking, Linear link
  synchronization, generated workflow privacy, README requirements, and
  version output.

## Validation

1. Baseline: `python3 -m unittest tests.test_symphonz_cli
   tests.test_symphonz_service.WorkflowServiceTests -v`: PASS, 43 tests.
2. Focused final: `python3 -m unittest tests.test_symphonz_cli
   tests.test_symphonz_service.WorkflowServiceTests -v`: PASS, 55 tests.
3. Complete required modules: `python3 -m unittest tests.test_symphonz_cli
   tests.test_symphonz_service -v`: PASS, 186 tests in 11.413s.
4. `PYTHONPYCACHEPREFIX=/tmp/symphonz-task6-pyc python3 -m py_compile
   symphonz/*.py symphonz/service/*.py tests/test_symphonz_cli.py
   tests/test_symphonz_service.py`: PASS.
5. `./bin/symphonz version`: PASS, exact output `symphonz 0.4.0`.
6. `sh -n install.sh`: PASS.
7. `git diff --check`: PASS.
8. Version, workflow personal-value, and implementation plaintext-password
   scans returned no matches in their intended scopes.

The existing event-sink isolation tests printed their expected diagnostic
messages while the complete suite exited successfully.

## Self-Review

- Confirmed the final changed implementation files are within Task 6
  ownership and no unrelated working-tree changes were present.
- Confirmed `configure-dashboard` leaves project workflow bytes unchanged and
  preserves non-dashboard TOML bytes in tests.
- Confirmed password values used by tests do not appear in production source,
  README, or WORKFLOW.
- Confirmed workflow fallback and guardrail rules no longer permit transition
  to `Human Review` without a review request and synchronized report.
- Confirmed legacy command construction retains the prior explicit `--port`
  argument shape while direct runtime invocation uses the loopback default.

## Concerns

- The separate static Chinese developer guide in `docs/index.html` still
  identifies itself as 0.3.1. That file was outside Task 6 ownership; README
  contains the 0.4.0 upgrade and dashboard guidance required by this task.
- HTTP remains intentionally limited to trusted LAN use. Public Internet
  exposure still requires separately managed TLS termination and security
  review, as documented.

## Important Review Fixes

- Added a private runtime-only compatibility mode for pre-0.4 configs with no
  `[dashboard]`. It activates only when `symphonz run` receives an explicit
  port, rejects non-loopback hosts in both the runner and dashboard server,
  emits a warning/event, and is not exposed by the direct `service` CLI.
  Configured dashboards continue to load and require `auth.toml`.
- Reworked dashboard section discovery around canonical bare and quoted TOML
  table headers with optional dotted keys and trailing comments. Migration now
  replaces a commented dashboard header cleanly, preserves following bare or
  quoted table headers, and rejects semantic dashboard aliases before password
  or file mutation.
- Added atomic fsync-and-replace writes for non-secret config, workflow, and
  gitignore content. Both installation flows render all content before file
  mutation, commit gitignore and non-secret files before auth, and retain the
  pinned-directory atomic auth writer unchanged. Dashboard reconfiguration
  restores the prior config atomically if auth commit fails.

### Review Fix TDD Evidence

1. The legacy execution RED test reached `read_dashboard_auth` and failed on a
   missing `auth.toml`; after the loopback-only compatibility implementation,
   the real `run_installed`/runner path exited cleanly without auth.
2. Anonymous dashboard boundary RED tests failed by dereferencing a missing
   auth service and accepting `0.0.0.0`; both passed after loopback validation
   and the internal anonymous request gate were added.
3. Commented-header RED tests left two dashboard sections, and duplicate
   definitions were accepted. Both passed after shared table-header scanning
   and pre-mutation duplicate rejection.
4. Atomic-order RED tests showed direct config truncation, no atomic helper,
   auth attempted before new config, and auth attempted before gitignore and
   workflow. All passed after staged atomic commits, auth-last ordering, and
   configure rollback.
5. Focused review-fix tests: PASS, 13 tests in 0.775s across CLI and
   dashboard boundary cases.
6. Complete CLI and service suites: PASS, 197 tests in 12.207s.
7. Dashboard non-socket handler, startup, and template suites: PASS, 10 tests.
   The complete 26-test dashboard run was attempted, but the managed sandbox
   rejected all 16 real HTTP cases at `127.0.0.1` bind setup with
   `PermissionError: [Errno 1] Operation not permitted`. An escalation request
   for the required loopback environment was rejected; no assertion failed
   after a successful bind.
8. `PYTHONPYCACHEPREFIX=/tmp/symphonz-task6-review-pyc python3 -m py_compile
   symphonz/*.py symphonz/service/*.py tests/test_symphonz_cli.py
   tests/test_symphonz_service.py tests/test_symphonz_dashboard.py`: PASS.
9. `./bin/symphonz version`: PASS, exact output `symphonz 0.4.0`.
10. `sh -n install.sh` and `git diff --check`: PASS.
11. Changed-file scope check: PASS; `docs/index.html` is unchanged.

## Re-review Fixes

- Replaced the dictionary-presence legacy decision with a shared conservative
  classifier. Only the exact generated pre-0.4 table/key layout can activate
  the loopback bridge. Semantic quoted dashboard roots, including Unicode
  basic-key escapes, remain configured and authenticated; dotted/implicit,
  array-table, duplicate, modified, and otherwise ambiguous shapes fail closed
  with `configure-dashboard` guidance.
- Made auth replacement transactional across the post-replace directory fsync.
  The pinned auth directory now snapshots the prior bytes and mode, restores
  them atomically after a durability failure, or removes a newly created auth
  file when no prior state existed. Commit and rollback failures are both
  surfaced, while the original commit failure remains the exception cause.
- Made temporary cleanup failures secondary to write/replace failures for both
  secret and non-secret atomic writers.
- Re-rendered the four protected `.symphonz` ignore patterns as the final
  matching rules on every update. Existing copies are removed first, making
  the result idempotent and preventing earlier negations from winning.

### Re-review TDD and Validation

1. Classifier RED tests reproduced unauthenticated legacy activation for
   `["dash\\u0062oard"]`, dotted/implicit dashboard tables, dashboard array
   tables, and modified no-dashboard configs. Focused GREEN plus surrounding
   config/runtime tests: PASS, 37 tests.
2. Auth RED tests proved post-replace fsync failure retained the rotated auth,
   configure restored only config, and cleanup could mask replace failure.
   Focused GREEN: PASS, 8 auth transaction tests and 7 combined durability and
   cleanup tests.
3. A real `git init` + `git check-ignore -q` RED test showed trailing
   negations overriding all four protected paths. GREEN confirms auth,
   artifacts, logs, and workspace files are effectively ignored and a second
   render is byte-identical.
4. Complete CLI and service modules: PASS, 208 tests in 16.086s.
5. Dashboard non-socket handler, startup, and template suites: PASS, 10 tests.
6. Python 3.9-compatible `py_compile`, `symphonz 0.4.0`, `sh -n
   install.sh`, and `git diff --check`: PASS.
7. Added production/report lines contain no local paths, personal endpoints, or
   plaintext test passwords. Test-only password fixtures remain confined to
   the injected failure cases.
8. `docs/index.html` remains intentionally deferred and unchanged.

## Final Important Review Fixes

- Tightened the runtime-only 0.3 compatibility classifier to require the
  exact generated table/key layout and generator-owned values: embedded
  runtime mode, `symphonz-internal` command, `.symphonz/workspace` workspace
  root, and `.symphonz/logs` logs root. Same-shape edits now fail closed before
  the service can activate the unauthenticated loopback bridge.
- Split strict runtime classification from dashboard migration validation.
  `configure-dashboard` now accepts no-dashboard configs with comments,
  custom sections, or noncanonical formatting, preserves every existing byte,
  and appends the generated dashboard section. Duplicate semantic dashboard
  roots, dotted/implicit tables, and dashboard array tables still fail before
  password resolution or file mutation.
- Made descriptor cleanup commit-aware across non-secret atomic writes, auth
  writes, auth snapshots, and auth restoration. Precommit close failures are
  secondary to the original exception. Once replace and required directory
  fsync succeed, close-only failures produce a non-raising, secret-free
  `RuntimeWarning`; `configure-dashboard` no longer rolls back committed config
  while leaving rotated auth in place.

### Final Review TDD and Validation

1. Four fixed-value RED subcases each reached `run_service` instead of failing
   closed. GREEN rejects modifications to runtime mode, runtime command,
   workspace root, and logs root while preserving exact generated 0.3 support.
2. Commented, custom-section, and noncanonically formatted no-dashboard RED
   migrations all stopped at the strict runtime classifier. GREEN migrates all
   three and verifies the original byte prefix is unchanged.
3. Injected close-failure RED tests showed raw descriptor cleanup masking
   config/auth primaries, auth rollback losing its preparation error, and
   committed config/auth directory closes triggering rollback. GREEN preserves
   causal primaries and treats committed close-only failures as warnings with
   no password text.
4. Focused config/runtime regressions: PASS, 52 tests in 5.970s.
5. Complete CLI and service modules: PASS, 216 tests in 16.145s.
6. Dashboard non-socket handler, startup, and template suites: PASS, 10 tests.
7. Python 3.9-compatible `py_compile`: PASS.
8. `./bin/symphonz version`: PASS, exact output `symphonz 0.4.0`.
9. `sh -n install.sh` and `git diff --check`: PASS.
10. Scope and privacy checks: PASS; only the report, installer module, and CLI
    tests changed, with no local paths or plaintext password fixtures in
    production/documentation/report files.
11. `docs/index.html` remains deferred and unchanged.

## Final Semantic TOML Gap

- Root-level TOML key definitions that semantically create `dashboard`, including
  bare dotted keys, quoted keys, Unicode-escaped quoted keys, and inline tables,
  are now detected before dashboard classification can select legacy mode.
- The scanner reuses the existing TOML key-path decoder for headers and key
  assignments. It only considers assignments before a table header to be root
  definitions, so `dashboard.*` keys under unrelated tables remain valid and
  byte-preserved by `configure-dashboard`.
- Any root dashboard definition, including one combined with canonical
  `[dashboard]`, now fails closed with resolution guidance before password
  prompting, `.gitignore`, config, or auth mutation.
- Added a direct `_write_synced_file` close-failure test proving an earlier
  write error remains the exception cause; existing committed-close tests
  continue to cover warning-only cleanup diagnostics after durable commits.

### Final Gap TDD and Validation

1. RED: root dotted and inline dashboard definitions were accepted by
   `configure-dashboard`, while runtime reached generic ambiguity handling
   instead of identifying their semantic dashboard definition.
2. GREEN focused tests: PASS, 5 tests covering bare, quoted, Unicode-escaped,
   and inline roots; canonical-plus-root conflict; no getpass or mutations;
   unrelated nested key preservation; and direct write/close failure precedence.
3. Complete CLI and service modules: PASS, 221 tests in 16.340s.
4. Dashboard non-socket handler, startup, and template modules: PASS, 10 tests.
