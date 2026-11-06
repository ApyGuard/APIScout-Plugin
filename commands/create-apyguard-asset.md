---
description: Open the ApyGuard asset creation form for this project.
---

Authenticate first if needed, then call `open_apyguard_asset_form` with the
absolute path of the current project. The rendered form collects the asset name,
analyzed API URL, description, environment, and tags. On submit it calls
`create_apyguard_asset`, which uploads the generated OpenAPI JSON using the
ApyGuard discovery request contract. If the form does not render or reports that
it could not reach the tool, collect those fields in chat and call
`create_apyguard_asset` once the user confirms them.
