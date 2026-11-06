---
name: apyguard-apiscout
description: Discover APIs from local backend source, review inventory findings, explain endpoints from evidence, export OpenAPI, compare OpenAPI documents, or list this project's ApyGuard asset and API collections with ApyGuard APIScout.
---

# ApyGuard APIScout

Use the APIScout MCP tools for local API discovery and OpenAPI workflows.

- Keep user-facing progress and final responses in the language of the user's
  request. Keep tool names and code identifiers unchanged.
- Resolve the current project directory to an absolute `project_path` before
  calling a project-scoped tool. Keep progress concise and product-oriented;
  do not narrate shell commands, cache paths, or implementation files.
- For asset requests such as "create an asset on ApyGuard", "open asset creation
  form", or "show the asset form", call `open_apyguard_asset_form` directly.
  Do not ask which asset type is intended or redirect the user to a browser
  page. The rendered form collects asset name, analyzed API URL, description,
  environment, and tags, and its submit action calls `create_apyguard_asset`.
  If the host does not render the form, or the form reports that it could not
  reach the tool, collect those fields in chat and call `create_apyguard_asset`
  once the user confirms them.
- The asset creation tool scans the project and sends the generated OpenAPI JSON
  to ApyGuard using the discovery-results request contract of the active client. It creates the
  asset only when the project has no linked asset; otherwise it imports the new
  documentation into the existing project asset. It is a write operation and
  must only run after the user submits the form or explicitly asks to create or
  import the asset documentation.
- When its `operation` is `already_imported`, ApyGuard already holds this exact
  documentation and imported nothing new: tell the user nothing has changed
  since the last import. When it reports that ApyGuard is already importing the
  asset, do not call it again right away: tell the user to wait for that import
  to finish.
- For requests to see what ApyGuard holds, such as "list the assets", "which
  asset is this project linked to", or "show the API collections", call
  `list_apyguard_assets` with `project_path` instead of opening the form. Present
  each asset's `name` and `api_url`, then each collection's `name`, `status`,
  `endpoint_count`, and `last_scan_date`. An asset's own `status` is `Unknown`
  unless ApyGuard reports one, so read the import state from its collections.
- That listing covers only this project's asset: one created from another
  checkout or client does not appear, so do not say the account has no other
  assets. An `asset_count` of 0 means no asset is linked to this project yet:
  say so and offer the asset form.
- For questions such as "has the import finished" or "is the live scan done",
  and after `create_apyguard_asset` imports documentation, call
  `get_apyguard_progress` with `project_path`. Report `state` for the import and
  the scan, with `progress` when there is one and `detail` when it failed. It
  reads once: when something is `running`, say so and offer to check again
  instead of calling it in a loop.

## Guided workflows

Move to a workflow skill when the request goes beyond reading results, and follow it from its first step:

- `review-and-fix`: the user wants findings changed in source, not only listed or explained, as in "fix these findings" or "bulguları düzelt".
- `full-pipeline`: the user wants static review through a live ApyGuard scan, or has live scan findings to map to source, as in "run the full pipeline" or "canlı tarama bulgularını düzelt".

Stay in this skill for a scan, findings list, endpoint explanation, export, comparison, or asset request that involves no source changes.

## User-facing command examples

Offer these copy-ready prompts when the user is new to the plugin:

```text
Scan this project with APIScout and summarize the API findings.
Open the APIScout Workbench for this project.
Show high-sensitivity API findings in this project.
Explain the GET /users/{user_id} endpoint using source evidence.
Explain the GET /users/{user_id} endpoint with AI and open its source.
Review the GET /users/{user_id} endpoint, suggest a correction, and add it as a TODO at the source line.
Export this project's OpenAPI document to openapi.json.
Compare /absolute/path/previous.json with /absolute/path/current.json.
Open the ApyGuard asset creation form for this project.
List the ApyGuard asset and API collections for this project.
Has the ApyGuard import or live scan of this project finished?
```

- Before every APIScout workflow, call `get_apyguard_auth_status`. On the first use after installation, or whenever `login_required` is true, call `start_apyguard_login`, present its `login_url` as a clickable link with its `user_code`, and ask the user to check that the browser page shows the same code before approving it. Stop the API operation until the user confirms the approval, then call `complete_apyguard_login` and repeat the requested APIScout operation. If it reports that the code is still waiting for approval, ask the user to approve it and try again.
- When a user asks to inspect a scan visually, review a summary, browse findings, or choose endpoint actions, call `open_api_workbench(project_path)` after login. It provides summary, findings, endpoints, evidence, and export controls in MCP Apps-compatible hosts; retain a concise text summary for hosts that do not render the component.
- The access token is stored only on this computer and never appears in tool results; never ask the user for it. When a tool reports that ApyGuard refused the sign-in, call `start_apyguard_login` again.
- Recognize both Turkish and English APIScout requests: "APIScout ile projeyi tara" / "Scan this project with APIScout", "Workbench'i aç" / "Open the Workbench", "Bulguları göster" / "Show findings", "OpenAPI dışa aktar" / "Export OpenAPI", "iki API dokümanını karşılaştır" / "compare two API documents", and "asset'leri ve collection'ları listele" / "list the assets and collections".
- Call `scan_api_project` before summarizing a project's APIs. Present the framework and operation count first, then high- and medium-sensitivity findings with source locations.
- Call `review_api_project` when the user asks for an AI-assisted or evidence-backed API review. It returns high- and medium-sensitivity findings with source excerpts and endpoint context in one response. Use the active model to interpret this evidence; do not call an external AI provider or request an AI key. Clearly separate facts from inference.
- Call `list_api_findings` when the user asks for filtered findings. Pass empty lists when no filter was requested. Endpoints missing from the project's OpenAPI document are listed only when `categories` includes `undocumented_endpoint`; `scan_api_project` reports how many there are in `undocumented_endpoint_count`, and they are documentation gaps rather than security findings.
- Call `get_endpoint_context` before explaining an endpoint. Base the explanation only on returned endpoint metadata, models, findings, provenance, and source excerpt. Clearly label any inference and do not invent missing behavior.
- Call `export_openapi` only after the destination and format are explicit. Warn that an existing destination is overwritten and rely on the client confirmation before the write.
- Call `compare_openapi` for two specifications and group its changes into endpoint, parameter, body, response status, and security changes.

Use these argument mappings when calling tools directly:

- `list_api_findings`: `sensitivity_levels` and `categories` are lists; use `[]` for either list when it is not filtered.
- `get_endpoint_context`: pass the HTTP method and exact route path; use `null` for `review_context` unless the user asks for a specific documentation review.
- `export_openapi`: pass an output path ending in `.json`, `.yaml`, or `.yml`; the path must be inside the scanned project.
- `compare_openapi`: pass two existing absolute document paths as `previous_document_path` and `current_document_path`.

All APIScout operations require an ApyGuard browser sign-in. Do not request AI provider keys; the active model writes endpoint explanations from the returned evidence.
