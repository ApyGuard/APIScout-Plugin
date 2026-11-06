# Changelog

Add a line under Unreleased with each user-visible change. The `release`
workflow moves those lines under the new version.

## Unreleased

## 0.1.9 - 2026-11-06

## 0.1.8 - 2026-11-06

### Changed

- Set the effective date of the privacy policy and terms of service to
  November 6, 2026.
- Point the Codex plugin's privacy policy and terms of service links at the
  plugin's own texts.

## 0.1.7 - 2026-11-06

### Security

- Require a `certifi` release without the compromised emSign root certificates,
  and refresh the locked dependencies with every release.
- Keep the runtime in the user's own cache instead of a predictable path in a
  shared temporary directory.
- Remove `APYGUARD_APISCOUT_ASSETS_URL` and
  `APYGUARD_APISCOUT_DISCOVERY_RESULTS_URL`, which could send the access token
  past the configured API. Use `APYGUARD_APISCOUT_API_URL`.
- Generated OpenAPI documents, whether exported or uploaded, hold source paths
  relative to the project instead of absolute local paths.

### Fixed

- Reviews no longer fail on a source file that is not UTF-8.
- A request that times out or loses its connection is retried and reported with
  its URL.
- An import of unchanged documentation is no longer reported as a new asset.
- The launcher finds `uv` in its installer's directories, and says how to
  install it when it is missing.

### Changed

- New installs get the latest tagged release instead of the `main` branch.
- The Linux engine wheel runs on glibc 2.28 and newer, where it needed 2.38.
- The tools that talk to ApyGuard declare it in their annotations.
- The skills use `get_apyguard_progress`, and fall back to chat when the asset
  form cannot reach its tool.

## 0.1.6 - 2026-11-05

Bundles engine 1.0.13.

### Added

- `get_apyguard_progress` reads the latest import and live scan of a project.
- The `review-and-fix` and `full-pipeline` skills.
- `compare_openapi` reports body, response status, and security changes.
- `list_api_findings` lists undocumented endpoints when that category is
  requested.

### Changed

- Sign-in uses ApyGuard's device flow with a token per installation.
- An access token is sent only to the API that issued it.
- A refused asset request is sent once and reports ApyGuard's reason.
- `create_apyguard_asset` needs only `project_path` for an import, and refuses a
  loopback `analyzed_api_url` for a new asset.

### Fixed

- A project without an asset is listed as empty instead of as an error.
- `list_apyguard_assets` shows why an import failed.
- `start_apyguard_login`'s instructions include the sign-in link.

## 0.1.5 - 2026-10-02

Bundles engine 1.0.11.

## 0.1.4 - 2026-09-30

First release. Bundles engine 1.0.10.
