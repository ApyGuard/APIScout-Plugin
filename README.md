# ApyGuard APIScout Plugin

Local Codex and Claude Code integration for ApyGuard APIScout. The plugin scans
backend source code, returns structured API inventory findings, prepares endpoint
evidence and exact source locations for the active agent, opens an interactive
asset creation form, exports OpenAPI, and compares OpenAPI documents.
It does not require VS Code, but it requires an ApyGuard browser sign-in before
scan, findings, endpoint context, export, or comparison tools can run.

## Table of Contents

- [Installation](#installation)
- [First use](#first-use)
- [What the plugin does](#what-the-plugin-does)
- [Where your data goes](#where-your-data-goes)
- [Known limitations](#known-limitations)
- [Updates](#updates)
- [Development and releases](#development-and-releases)
- [Security and support](#security-and-support)
- [Legal](#legal)

## Installation

### Requirements

- Codex (CLI or desktop app) or Claude Code (CLI or desktop app)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), which also
  fetches the Python 3.13 runtime the plugin needs
- A supported platform:

| Platform | Support |
|---|---|
| macOS on Apple silicon | Supported |
| macOS on Intel | Supported |
| Linux x86-64 | Supported with glibc 2.28 or newer, such as AlmaLinux 8, Ubuntu 22.04, or Debian 12 |
| Windows | Through WSL, with a distribution that meets the Linux requirement |
| Linux arm64 | Not supported |

The MCP server launcher is a POSIX shell script, so native Windows is not
supported yet. Run Claude Code inside WSL, or switch the Codex desktop app's
agent to WSL in Settings, and install `uv` inside WSL.

The plugin's Python packages are not published on PyPI. It installs them only from
the wheels in this repository, so do not install `apyguard-engine` or
`apyguard-apiscout-plugin` from a package index.

### Codex

Add the public ApyGuard plugin marketplace and install the plugin:

```bash
codex plugin marketplace add ApyGuard/APIScout-Plugin
```

```bash
codex plugin add apyguard-apiscout-plugin@apyguard-apiscout
```

If macOS reports `codex: command not found` while using the Codex desktop app,
add the bundled CLI to the current shell session and run the commands again:

```bash
export PATH="/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin:$PATH"
```

The plugin can also be installed from the Plugins screen in the Codex desktop
app by adding the public `ApyGuard/APIScout-Plugin` marketplace.

Once the marketplace is added, this link opens the plugin's install screen in
the Codex desktop app. Paste it into your browser, or run `open "<link>"` on
macOS:

```text
codex://plugins/install/apyguard-apiscout-plugin?marketplace=apyguard-apiscout
```

### Claude Code

Add the same marketplace and install the plugin for your user:

```bash
claude plugin marketplace add ApyGuard/APIScout-Plugin
```

```bash
claude plugin install apyguard-apiscout-plugin@apyguard-apiscout
```

Claude Code does not update plugins from third-party marketplaces unless
auto-update is enabled for that marketplace. To receive new releases
automatically, run `claude` in a terminal, open `/plugin`, go to
**Marketplaces**, select `apyguard-apiscout`, and choose **Enable auto-update**.
The setting also applies to the Code tab of the Claude desktop app.

To try a local checkout without installing it, start Claude Code from the
project you want to scan and load the plugin directory for that session only:

```bash
claude --plugin-dir /absolute/path/to/apyguard-apiscout-plugin
```

## First use

After installation, start a new chat in your client and paste one of these
prompts:

### Scan the project

```text
Scan this project with APIScout and summarize the API findings.
```

### Open the Workbench

```text
Open the APIScout Workbench for this project.
```

### Show high-sensitivity findings

```text
Show high-sensitivity API findings in this project.
```

### Explain an endpoint

```text
Explain the GET /users/{user_id} endpoint using source evidence and open its source.
```

### Review an endpoint and add a TODO

```text
Review the GET /users/{user_id} endpoint, suggest a correction, and add the suggestion as a TODO at its source line.
```

### Fix findings in source

```text
Review and fix this project's APIScout findings.
```

### Run the full pipeline

```text
Run the full API security pipeline with APIScout for this project.
```

### Export OpenAPI

```text
Export this project's OpenAPI document to openapi.json.
```

### Compare OpenAPI documents

```text
Compare /absolute/path/previous.json with /absolute/path/current.json.
```

### Open the asset creation form

```text
Open the ApyGuard asset creation form for this project.
```

### List the asset and its collections

```text
List the ApyGuard asset and API collections for this project.
```

### Check an import or a live scan

```text
Has the ApyGuard import or live scan of this project finished?
```

Use the project root as the path when a prompt needs an explicit path. The
first APIScout request starts the browser login automatically when needed; after
returning from the connected page, repeat the prompt. Claude Code users can
also use the bundled commands shown by the plugin command list.

In MCP Apps-compatible clients, `open_api_workbench` adds a compact interactive
workbench with scan summary metrics, finding severity filters, endpoint evidence
actions, and an export entry point. The separate asset form collects metadata and
uploads the generated OpenAPI document. The same tools remain structured results
in text-only clients, including CLI-oriented environments.

The MCP server signs in to ApyGuard with a device sign-in. `start_apyguard_login`
returns a login URL and a short code; you sign in to ApyGuard in the browser,
check that the page shows the same code, and approve it. The next APIScout
request finishes the sign-in and receives an access token for this installation
of the plugin. Tokens are stored outside the repository in separate owner-only
files under `~/.config/apyguard/apiscout/`, stop working after 90 days without
use, and can be revoked from ApyGuard's integration settings; `sign_out_apyguard`
revokes the token. Set `APYGUARD_APISCOUT_API_URL` to use another ApyGuard API,
such as an on-premises installation. The token is sent only to the API that
issued it.

## What the plugin does

- Scans local backend source code and summarizes its API surface.
- Reviews inventory findings with source-backed endpoint evidence.
- Opens the interactive APIScout Workbench.
- Fixes approved findings in source and verifies them with a rescan, through the
  `review-and-fix` skill.
- Takes a project from static review through an ApyGuard live scan and back to
  source, through the `full-pipeline` skill. You start the scan in ApyGuard and
  share its findings.
- Creates one ApyGuard asset per project and imports later documentation into it.
- Lists a project's asset and collections, and reports whether its latest import
  and live scan have finished.
- Exports and compares OpenAPI documents.

## Where your data goes

**On your computer.** The MCP server and the APIScout engine run locally. Scans,
exports, and comparisons read and write local files, and do not upload them. The
plugin stores its runtime under `~/.cache/apyguard/apiscout/`, or under
`XDG_CACHE_HOME` when that is set.

**What the plugin downloads.** The first time the plugin starts, `uv` builds its
runtime. The plugin and the engine come from the wheels in this repository. The
third-party Python packages they depend on come from PyPI
(`files.pythonhosted.org`), pinned to the exact versions and hashes in the
platform's `uv.lock`. If Python 3.13 is not installed, `uv` also downloads it from
Astral's `python-build-standalone` releases on GitHub. Later starts reuse the
runtime, and an update downloads only the packages that are not already in `uv`'s
cache.

**To your AI model provider.** Tool results become part of your Codex or Claude
Code conversation, and your client sends them to its model provider under that
provider's terms. These results include API routes, findings, local file paths,
and, from `review_api_project` and `get_endpoint_context`, source code excerpts of
up to 12,000 characters per endpoint. The plugin does not call an AI provider
itself. Do not scan code that your agreement with your model provider does not
allow you to share.

**To ApyGuard.**

- Signing in sends the client type and a random installation id.
- Listing an asset or reading progress sends an identifier derived from the
  project's absolute path.
- Creating an asset or importing documentation also sends the asset details you
  enter and the generated OpenAPI document. That happens only when you submit the
  form or ask for it. The document holds routes, schemas, descriptions taken from
  the code, findings, and source file paths relative to the project, but not source
  files or excerpts.

See the [Privacy Policy](legal/privacy-policy.md) for how ApyGuard handles this
information.

## Known limitations

- **Asset form in the Claude desktop app's Code tab.** The form renders, but
  submitting it can fail with `IPC not available for MCP tool call`. Ask the
  agent in chat to create the asset instead, or use the Chat tab.
- **Assets follow the project's path and client.** An asset belongs to the
  project's absolute path and to the client that created it, so another checkout
  or client does not see it.
- **Authentication evidence.** Only the `django` framework reports endpoint
  authentication today. For other frameworks, a missing value says nothing about
  whether the endpoint is protected.

## Updates

Each release is tagged `plugin-v<version>` in this repository, for example
`plugin-v0.1.6`, and new installs get the latest tagged release. The
[changelog](CHANGELOG.md) lists what changed. Claude Code updates the plugin on
its own only when auto-update is enabled (see [Claude Code](#claude-code));
otherwise, and for Codex, follow the steps for the client you use.

### 1. Check your installed version

```bash
claude plugin list | grep -A3 apyguard
```

```bash
ls ~/.codex/plugins/cache/apyguard-apiscout/apyguard-apiscout-plugin/
```

The first command shows the Claude Code version, the second lists the Codex
versions on disk. If it already matches the latest `plugin-v*` tag, you are up
to date.

### 2. Update the plugin

**Claude Code** (terminal and the Code tab of the Claude desktop app):

```bash
claude plugin marketplace update apyguard-apiscout
```

```bash
claude plugin update apyguard-apiscout-plugin@apyguard-apiscout
```

**Codex** (CLI and desktop app). If `codex` is not found, add the bundled CLI to
the current shell first:

```bash
export PATH="/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin:$PATH"
```

```bash
codex plugin marketplace upgrade apyguard-apiscout
```

```bash
codex plugin add apyguard-apiscout-plugin@apyguard-apiscout
```

### 3. Restart the client

- **Claude Code**: quit the Claude desktop app completely (Cmd+Q on macOS) and
  reopen it, or start a new `claude` session in the terminal.
- **Codex**: start a new chat so the new skills and MCP tools are loaded.

### 4. Confirm the update

Run the version check from step 1 again; it should show the new version. Then
try a prompt such as `Scan this project with APIScout and summarize the API findings.`
The first request after an update can take a few seconds longer while the new
runtime is prepared. Your ApyGuard login is kept, so you do not need to sign in
again.

### If the update does not apply

- **Claude Code says `already at the latest version`**: run
  `claude plugin marketplace update apyguard-apiscout` again, then retry the
  update. If it still shows the old version, the new release has not been
  published yet.
- **Codex keeps the old version**: remove the plugin and add it again:

  ```bash
  codex plugin remove apyguard-apiscout-plugin@apyguard-apiscout
  ```

  ```bash
  codex plugin add apyguard-apiscout-plugin@apyguard-apiscout
  ```

- **APIScout tools are missing after the update**: make sure you restarted the
  client (step 3); a running session keeps the version it started with.

## Development and releases

Repository layout:

```text
apyguard-apiscout-plugin/
├── src/                 # Local MCP package and UI resources
├── skills/              # Agent skills for both clients
├── commands/            # Claude Code commands
├── scripts/             # MCP launcher and release scripts
├── tests/
├── legal/               # Plugin privacy policy and terms of service
├── .claude-plugin/      # Claude Code manifest, MCP configuration, and marketplace
├── .codex-plugin/       # Codex plugin manifest
├── .mcp.json            # MCP configuration for Codex
└── releases/            # Per-platform runtime lockfiles and plugin/engine wheels
    ├── darwin-arm64/
    ├── darwin-x64/
    ├── linux-x64/
    └── win32-x64/
```

Installed copies use only the wheels and lockfile under `releases/<platform>/`.
The launcher picks them for the current platform. Codex starts the server from the
root `.mcp.json` as the `codex` client. Claude Code reads
`.claude-plugin/plugin.json`, which points to `.claude-plugin/.mcp.json` and starts
the same server as the `claude_code` client.

### Running the tests

The engine is not on a package index, so `uv sync` does not work at the repository
root. Run the tests in your platform's runtime, with this checkout's source on the
path:

```bash
PYTHONPATH=src uv run --project releases/darwin-arm64/runtime --locked --with 'pytest>=8.3,<9' python -m pytest -q -p no:cacheprovider
```

The `ci` workflow runs the same command on every pull request.

### Releases

Codex and Claude Code install a new copy only when the plugin version changes,
so every release increases the version. The `release` workflow in this
repository is the only writer of the version and of `releases/`:

- **Engine releases**: a new engine release builds the wheels for all four
  platforms and sends an `engine-released` event here. The `release` workflow then
  bundles those wheels and increases the patch version.
- **Plugin-only releases**: run the `release` workflow manually from the
  Actions tab. Choose the version part to increase, and leave `engine_tag`
  empty to keep the current engine wheels.

Each run increases the version and rolls the changelog's Unreleased section
(`scripts/bump_version.py`), syncs both manifests and points the marketplace at the
new tag (`scripts/sync_version.py`), builds the plugin wheel once, places it with
each platform's engine wheel under `releases/` (`scripts/assemble_release.py`), and
relocks each runtime with the latest allowed dependencies. It then runs the tests
and `scripts/smoke_mcp.py` against the linux-x64 runtime, commits, tags
`plugin-v<version>`, pushes to `main`, and publishes a GitHub release. Runs are
serialized, and the push fails instead of overwriting when `main` moved during the
run.

Merged changes reach users only through a release, because the marketplace points
at the release tag.

Required secrets:

- `ENGINE_RELEASE_TOKEN` in this repository: a fine-grained token with
  read-only Contents access to the engine repository, used to download engine
  wheels from its releases.
- `APISCOUT_PLUGIN_TOKEN` in the engine repository: a token with Contents write
  access to this repository, used to send the `engine-released` event.

## Security and support

- Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
- For bugs and questions, open an issue in this repository or write to
  [support@apyguard.com](mailto:support@apyguard.com).

## Legal

- [ApyGuard APIScout Plugin Privacy Policy](legal/privacy-policy.md)
- [ApyGuard APIScout Plugin Terms of Service](legal/terms-of-service.md)
- [License](LICENSE): the plugin's own code is licensed under the Apache License,
  Version 2.0. The bundled APIScout Engine wheels and the ApyGuard and APIScout
  logos are not; [NOTICE](NOTICE) lists them and the terms that apply instead.

The Plugin is intended for authorized API discovery and documentation work. Do
not scan source code, APIs, or systems that you do not own or have permission
to assess.
