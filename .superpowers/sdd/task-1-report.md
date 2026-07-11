# Task 1 Report: Linear Adapter and Domain Contracts

## Scope Audited

- `symphonz/service/models.py`
- `symphonz/service/linear.py`
- `symphonz/service/dynamic_tools.py`
- `tests/test_symphonz_service.py`

## Initial Audit

- Read `/tmp/symphonz-runtime-hardening/.superpowers/sdd/task-1-brief.md`.
- Read `docs/superpowers/specs/2026-07-11-symphonz-runtime-hardening-design.md`.
- Audited the uncommitted diff and untracked `symphonz/service/dynamic_tools.py`.
- No prior `task-1-report.md` existed, so there was no trustworthy RED/GREEN record from the previous agent.

## Observed Failures

1. The instructed focused suite was already green on arrival:
   - `python3 -m unittest tests.test_symphonz_service.LinearAndWorkspaceTests -v`
2. Because prior RED evidence was missing, I added an edge-case test to verify the dynamic-tool contract more rigorously:
   - `test_linear_graphql_tool_allows_keywords_inside_string_literals`
3. That new test failed before the fix:
   - Command: `python3 -m unittest tests.test_symphonz_service.LinearAndWorkspaceTests.test_linear_graphql_tool_allows_keywords_inside_string_literals -v`
   - Failure: `AssertionError: False is not true`
   - Root cause: `_has_one_operation()` counted `query` / `mutation` keywords with a regex, which incorrectly treated GraphQL argument names or string literals as additional operations.

## Changes Made

- Kept the existing pagination and blocker normalization work after audit:
  - Added `BlockerRef` and `Issue.blocked_by`.
  - Added paginated `LinearClient.fetch_candidate_issues()`.
  - Added paginated `LinearClient.fetch_issues_by_states()`.
  - Kept pagination guard for `hasNextPage=true` without `endCursor`.
- Kept the new dynamic-tool surface:
  - `execute_linear_graphql(client, arguments)`
  - `linear_graphql_tool_spec()`
- Hardened dynamic-tool operation validation:
  - Replaced regex-only operation counting with a lightweight document scanner that strips comments and string literals, then counts actual top-level operation definitions.
- Added tests for:
  - candidate pagination across two pages
  - blocker normalization from `inverseRelations`
  - state-based pagination
  - missing `endCursor` rejection
  - dynamic-tool multiple-operation rejection
  - structured mutation success response
  - keywords inside GraphQL string literals

## GREEN Verification

1. `python3 -m unittest tests.test_symphonz_service.LinearAndWorkspaceTests.test_linear_graphql_tool_allows_keywords_inside_string_literals -v`
   - Result: `Ran 1 test ... OK`
2. `python3 -m unittest tests.test_symphonz_service.LinearAndWorkspaceTests -v`
   - Result: `Ran 10 tests ... OK`
3. `python3 -m unittest tests.test_symphonz_service -v`
   - Result: `Ran 16 tests ... OK`
4. `git -C /tmp/symphonz-runtime-hardening diff --check`
   - Result: clean

## Files Changed

- `symphonz/service/models.py`
- `symphonz/service/linear.py`
- `symphonz/service/dynamic_tools.py`
- `tests/test_symphonz_service.py`
- `.superpowers/sdd/task-1-report.md`

## Commit

- Implementation commit before report self-reference reconciliation:
  - `ed9fbdf` `Implement linear adapter domain contracts`
- The final report-containing commit SHA is returned in the task handoff response because embedding a commit's own SHA inside the committed report would change the SHA again.

## Concerns

- The previous agent left no Task 1 report or other durable RED evidence. I verified current behavior, audited the diff, and created a fresh RED/GREEN cycle for the dynamic-tool parser edge case, but I could not directly confirm that the earlier pagination/blocker tests were ever observed failing before their implementation.
- The report cannot contain the exact final report-containing commit SHA without creating an infinite self-reference loop; the final HEAD SHA must be read externally after the commit is created.
