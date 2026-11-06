---
name: review-and-fix
description: Use when the user wants APIScout findings checked against source code, fixed, and verified by a rescan. Triggers include "review and fix API findings", "fix APIScout findings", "fix the API security findings", "fix and verify findings", "bulguları incele ve düzelt", "API bulgularını düzelt", "APIScout bulgularını düzelt", "güvenlik bulgularını düzelt".
---

# APIScout Review and Fix

Checks each APIScout finding against source evidence, changes only what the user approves, then rescans and reports what the evidence shows. This flow is static: it shows what the source declares, not how the running API behaves. For a live scan, use the `full-pipeline` skill.

- Keep user-facing progress and the final summary in the language of the user's request. Keep tool names and code identifiers unchanged. Keep progress concise and do not narrate shell commands.
- Each approval point below means: ask the user and wait for an explicit answer.
- Leave every change uncommitted. Commit, stash, or reset only when the user asks.

## Reading the evidence

`endpoint.security` from `get_endpoint_context` tells you only this:

| Value | What the scan found | What it does not show |
|---|---|---|
| `null` | No auth information. Today only the `django` framework fills this field, so every other framework returns `null` whether or not the handler is protected. | That the endpoint is unprotected. |
| `[]` | Explicitly public, such as DRF `AllowAny`. | That public access is wrong. |
| `bearerAuth` | Authentication is required, such as DRF `IsAuthenticated`. The name is a label; the project may not use bearer tokens. | Role or ownership checks. |
| `unknownAuth` | A custom permission the parser cannot interpret. | Whether it admits this caller. |

Other limits of the evidence:

- Findings come from path, name, and model signals, not from auth. `sensitivityLevel` ranks how sensitive a route looks, not whether it has a defect.
- `source_excerpt` shows the lines around the handler. Auth can live outside it: router dependencies, middleware, filter chains, settings. Look there before concluding that auth is missing.
- `source_location` and `source_excerpt` can be `null`, as on every Express and oRPC endpoint today. Then read `endpoint.file_path` and find the route yourself.
- `source_location.start_line` is the first line that names the handler, which can be an import or the enclosing class, as with DRF viewsets. Open the file and confirm the line before citing it.
- `compare_openapi` reports endpoint, parameter, request and response body, response status, and security changes. In a body it compares property names, types, and required flags, not descriptions or validation limits.

## 1. Record the starting point

1. Call `get_apyguard_auth_status`. When `login_required` is true, call `start_apyguard_login`, present its `login_url` as a clickable link with its `user_code`, and ask the user to check that the browser page shows the same code before approving it. Stop until the user confirms the approval, then call `complete_apyguard_login` and continue. If it reports that the code is still waiting for approval, ask the user to approve it and try again.
2. Resolve the project directory to an absolute `project_path` and pass it to every project-scoped tool.
3. Note the git state so changes can be reverted: the current commit and the `git status --short` output. Keep files that were already modified apart from your fixes in the report. If the project is not a git repository, tell the user that reverting will be manual.
4. Call `review_api_project`. Record `finding_count` and the count per `sensitivityLevel` from `findings`.
5. Call `export_openapi` with `output_path` set to `<project_path>/.apyguard/review/before.openapi.json` and `output_format` set to `json`. If that file already exists, tell the user an earlier run's export is being replaced.

## 2. Verify and tier the findings

Check every finding against its evidence. `review_items[].endpoint_context` is what `get_endpoint_context` returns and carries all of that endpoint's findings, so reuse it. Call `get_endpoint_context`, with `review_context` set to `null`, only for endpoints without a review item, such as those with low-sensitivity findings; every tool call rescans the project.

Give each finding one tier, decided by the evidence rather than by `sensitivityLevel`:

- **Must fix**: the source shows a concrete defect with a clear fix in this project. Examples: a destructive or administrative route with no auth anywhere in its chain while the project has an auth mechanism, `security: []` on a route that handles private data, a duplicate route.
- **Needs verification**: the evidence cannot settle it. Examples: `null` or `unknownAuth` with enforcement you could not locate, object access by `{id}` with no visible ownership check, a role-sensitive action with no visible role check, a route that may be public on purpose.
- **Informational**: the evidence shows no defect. Examples: a name-based personal data signal on a route with visible auth, an older API version still exposed.

Present one table row per finding: endpoint, tier, **Fact** (what the tools returned or the source shows, with `file:line`), and **Inference** (what you conclude from it). Word defects as "source evidence suggests". Static evidence is never a verified vulnerability; reserve "confirmed" for a live test result.

## 3. Ask for approval

Ask the user and wait for an explicit answer with three options:

- **Fix all**: every Must fix item.
- **Fix selected**: only the items the user names. This is the only way to change a Needs verification item.
- **Review only**: no source changes, no rescan, and no second export. Go straight to step 6.

Any other reply, or no reply, is Review only; "looks reasonable" and "ok" are examples. Do not ask again: say that you took Review only and how to request fixes.

## 4. Fix the approved items

- Use the framework's own mechanism and the auth helpers the project already uses. Search the project for them first and apply them the way neighbouring routes do.
- Make the smallest change that resolves each approved item, and change only approved items.
- Keep paths, methods, and route registrations as they are. The one exception is removing a duplicate route.
- If the project has no auth mechanism, add nothing: no new scheme, middleware, library, or secret. List the item under "Decisions needed" with what the user must choose.
- If a fix changes what clients send or receive, such as removing a response field, say so in the report.
- If the project has tests, run them afterwards and report the command and result. Report a failing test as it is. If there are no tests, say so.

## 5. Verify

1. Call `review_api_project` again and record the counts. Call `export_openapi` with `output_path` set to `<project_path>/.apyguard/review/after.openapi.json`.
2. Call `compare_openapi` with the two absolute export paths. Report any change that an approved fix does not explain as unintended, ahead of everything else.
3. For every fixed endpoint, call `get_endpoint_context` and read `endpoint.security` with the table above:
   - `framework` is not `django`: `null` before and after is expected and says nothing about the fix, and more scans or exports will not change it. Confirm the fix in `source_excerpt` or in the file, then report "visible in source; the scan does not report auth for this framework".
   - `framework` is `django`: expect the value to change, for example from `[]` to `bearerAuth`. If it does not, tell the user the scan does not recognize the fix and show the source.
   - Role and ownership checks never appear in `security`: confirm them in the source.
4. Tell the user the finding count may not drop. Findings come from path, name, and model signals, so adding auth leaves them in place, and a flat count does not mean a fix failed.

## 6. Report

Use this shape. Omit the After column and the endpoint table for Review only.

```text
APIScout review and fix
  Outcome:  <N of M approved items fixed> / Review only
  Tests:    <command and result> / none found / not run (Review only)
  Git:      <starting commit>; <N> files changed, not committed
```

| Measure | Before | After |
|---|---|---|
| Operations | | |
| Findings | | |
| High / medium / low | | |

| Endpoint | Change (`file:line`) | `security` before → after | Source check | Contract change |
|---|---|---|---|---|

Follow with "Decisions needed" and "Needs verification" lists, then state that this is static evidence and that a live scan is what tests behaviour, through the `full-pipeline` skill.

Finish by asking the user and waiting for an explicit answer on deleting the export files this run created in `.apyguard/review/`. Recommend deleting them: they are this run's working files, and they hold source file paths and findings. Without a clear yes, keep them and remind the user to keep them out of commits.
