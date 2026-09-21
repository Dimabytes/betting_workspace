NO COMMENTS

# US-004 review — Compose onchain service, import CLI, local live Alchemy day

Commit `0a4b00b` in `polymarket-collector`. Process edge, import command, and Compose wiring sit in the right modules. No structural regression, no file crosses 1k lines (`onchain-import.ts` 732, `onchain-app.ts` 468), no spaghetti grafted onto collect/compact, no poly-maker touch.

Honor constraints hold:

- Two independent Alchemy endpoints (`ONCHAIN_RPC_URL_A`/`_B`, separate CU/concurrency/quota, pool `a`/`b`). Compose interpolates env names with `:-`; values never appear in YAML.
- URLs stay `Redacted`. Logs/Telegram use labels only (`rpcEndpoint: config.label`, `formatOnchainPass` endpoint lines). Tests pin `https://` absent from notify text and blank-URL errors.
- Compact `schema/manifest.ts` is untouched; `Manifest` still rejects channel `onchain_fills`.
- `poly-maker` is not in this commit.

`onchain-main.ts` mirrors `main.ts`. `OnchainDuckDbLive` correctly avoids `DuckDbSettingsLive` / `POLYMARKET_TAG_ID`. Import is a second image command, dry-run skips the lock, `full_day` is ready-manifest coverage only, empty legacy is `legacy_empty_unverified`. Compactor sweeps still ignore `.onchain/`. The `{ plans, outcomes }` + chunk-count change is additive, not a second error-isolation model.

No code-judo move that would delete a layer. Approve as implemented.
