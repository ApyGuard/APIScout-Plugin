---
name: full-pipeline
description: Use when the user wants the end-to-end API security flow with APIScout, from static review through an ApyGuard live scan, or has live scan findings to map back to source code. Triggers include "full API security pipeline", "run the full pipeline", "end-to-end API security", "map live scan findings to code", "fix live scan findings", "tam güvenlik akışı", "uçtan uca API güvenlik taraması", "canlı tarama bulgularını koda eşle", "canlı tarama bulgularını düzelt".
---

# APIScout Full Pipeline

Runs the static review, links the project to an ApyGuard asset, takes live scan findings from the user, maps them to source, fixes what the user approves, and verifies. No APIScout tool starts a live scan or returns its findings, so the user runs the scan in ApyGuard and brings the findings into this flow. `get_apyguard_progress` shows whether the scan has finished.

- Keep user-facing progress and the final summary in the language of the user's request. Keep tool names and code identifiers unchanged. Keep progress concise and do not narrate shell commands.
- Each approval point below means: ask the user and wait for an explicit answer.
- Leave every change uncommitted. Commit, stash, or reset only when the user asks.
- Report live results only from what the user shares; when none are shared, say the live phase has not run. To reproduce a finding, ask the user: sending requests to the API yourself can repeat a destructive action.
- Read tool evidence with the "Reading the evidence" section of the `review-and-fix` skill (`../review-and-fix/SKILL.md`).

## 1. Discover

1. Call `get_apyguard_auth_status`. When `login_required` is true, call `start_apyguard_login`, present its `login_url` as a clickable link with its `user_code`, and ask the user to check that the browser page shows the same code before approving it. Stop until the user confirms the approval, then call `complete_apyguard_login` and continue. If it reports that the code is still waiting for approval, ask the user to approve it and try again.
2. Resolve the project directory to an absolute `project_path` and pass it to every project-scoped tool.
3. Call `scan_api_project`. Summarize `framework`, `operation_count`, `finding_count`, and the high- and medium-sensitivity counts, then list the phases ahead: static review, asset, live scan in ApyGuard, mapping and fixes, verification.

## 2. Run the static phase

Run the `review-and-fix` flow (`../review-and-fix/SKILL.md`) unless the user asks to skip it. Sign-in and `project_path` are already done, and all of its approval points apply. Hold its deletion question until step 8 of this flow.

## 3. Link the asset

1. Call `list_apyguard_assets`. An `asset_count` of 0 means no asset is linked to this project yet: treat it as the no-asset case. Report any error and stop this phase. An asset belongs to the project's absolute path and to the client (Codex or Claude Code), so one created from another checkout or client does not appear here.
2. Tell the user that `analyzed_api_url` cannot be localhost, because ApyGuard scans the API remotely: it needs a staging URL or port forwarding.
3. With no asset, call `open_apyguard_asset_form`. Submitting the form calls `create_apyguard_asset`, so do not call it yourself. Wait for the user to say the form was submitted, then call `list_apyguard_assets` to confirm. If the host does not render the form, or the form reports that it could not reach the tool, collect the asset name, analyzed API URL, description, environment, and tags in chat, then call `create_apyguard_asset` once the user confirms them.
4. With an asset, show its `name`, `api_url`, and `status`, and warn if `api_url` is localhost. Ask the user and wait for an explicit answer on importing the new documentation. On a clear yes, call `create_apyguard_asset` with only `project_path`: an import sends only the documentation and leaves the asset's fields as they are. Without a clear yes, skip the import and say that ApyGuard keeps the documentation it already has.
5. When `operation` is `already_imported`, tell the user nothing has changed since the last import. When the tool reports that ApyGuard is already importing the asset, do not call it again right away: tell the user to wait for that import to finish. When `visibility_verified` is false, say ApyGuard accepted the request and the listing has not caught up yet. After an import, call `get_apyguard_progress` once and report its `import_progress`. When its `state` is `failed`, report `detail` and stop this phase; when it is `running`, say so and offer to check again instead of calling it in a loop.

## 4. Get the live scan results

Tell the user that this plugin cannot start a live scan or read its findings. Ask them to start the scan in ApyGuard and, when it finishes, to share each finding's method, path, vulnerability type, severity, and request and response evidence. Then wait.

- When the user asks whether the scan has finished, call `get_apyguard_progress` once and report its `scan_progress`. When its `state` is `running`, say so and offer to check again instead of calling it in a loop. A `completed` state says only that the scan ended: the findings still come from the user.
- Severity counts alone, such as "3 high, 5 medium", cannot be mapped to code. Say so, ask for the per-finding details, and make no edits.
- If the user stops here, go to step 8 and report the live phase as not run.

## 5. Map live findings to source

For each finding:

1. Match the concrete path to a template from `openapi.paths` in the latest `scan_api_project` result. Drop the scheme and host, ignore a trailing slash, and require the same method and segment count: `GET /users/42` maps to `GET /users/{user_id}`. Prefer a literal segment over a parameter. The match is your inference, so say so.
2. Call `get_endpoint_context` with the method, the exact template, and `review_context` set to `null`.
3. Open the file and confirm the handler line. When `source_location` is `null`, read `endpoint.file_path` and find the route.

Present mapped findings side by side:

| Live finding | Severity | Endpoint | Handler (`file:line`) | Live evidence (fact, from the user) | Source shows (fact) | Inference |
|---|---|---|---|---|---|---|

List findings with no matching method and template separately as "Unmapped", without forcing a match. They may belong to a gateway, another service, or a route the scan did not discover. Ask the user where they live, and fix nothing for them.

## 6. Ask for approval and fix

Ask the user and wait for an explicit answer: fix all mapped findings, fix selected ones, or make no fixes. Any other reply, or no reply, means no fixes: say so and go to step 8.

Before editing, note the git state again and call `export_openapi` with `output_path` set to `<project_path>/.apyguard/review/before-live.openapi.json` and `output_format` set to `json`.

The rules in step 4 of `review-and-fix` apply. By finding type:

- **BOLA**: add an ownership check that compares the resource's owner with the authenticated principal the project already exposes.
- **BFLA**: add a role check with the project's existing role or permission mechanism.
- **Data exposure**: narrow the response model to the fields the operation needs, and report this as a change clients can see.
- **Any other type**: describe the proposed fix in the approval question.

If the project has no principal or role mechanism to use, add nothing and list the finding under "Decisions needed".

## 7. Verify

1. Call `review_api_project` and record the counts. Call `export_openapi` with `output_path` set to `<project_path>/.apyguard/review/after-live.openapi.json`.
2. Call `compare_openapi` with the two absolute `-live` export paths, and report any change a fix does not explain as unintended. A narrowed response appears as `bodyPropertyRemoved`.
3. Call `get_endpoint_context` for each fixed endpoint and confirm the ownership or role check in the source. These checks never appear in `endpoint.security`, and finding counts do not reflect them.
4. Run the project's tests and report the command and result. If there are no tests, say so.
5. Ask the user and wait for an explicit answer on importing the new documentation, then handle it as in step 3.
6. Ask the user to deploy the fixes to the environment behind `analyzed_api_url` and run the live scan again. Source checks and tests show the code changed; only the new live scan shows the behaviour changed. Report each fix as "fixed in source, not yet confirmed live" until the user shares those results, then mark each finding "no longer reported" or "still reported".

## 8. Report

Use this shape, with one row per live finding:

```text
APIScout full pipeline
  Static phase:  <N of M approved items fixed> / Review only / Skipped
  Asset:         <name>: created / imported / already imported / import skipped
  Live scan:     <N findings received> / not run
  Mapping:       <N> mapped, <N> unmapped
  Tests:         <command and result> / none found
  Git:           <starting commit>; <N> files changed, not committed
```

| Live finding | Severity | Endpoint | Fix (`file:line`) | Source check | Contract change | Live status |
|---|---|---|---|---|---|---|

Follow with the static phase's before and after counts, then "Unmapped", "Decisions needed", and "Needs verification" lists.

Finish by asking the user and waiting for an explicit answer on deleting the export files in `.apyguard/review/`. Recommend deleting them: they are this run's working files, and they hold source file paths and findings. Without a clear yes, keep them and remind the user to keep them out of commits.
