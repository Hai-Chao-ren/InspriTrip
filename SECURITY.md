# Security and data boundary

## Public repository policy

This repository must not contain:

- API keys, access tokens, database credentials or filled `.env` files.
- Scraped UGC text, real source URLs, author identifiers or private analytics.
- Local snapshots, model caches, map caches or generated production datasets.
- Absolute local workspace paths.

The records in `data/demo/` are fictional and use `demo://synthetic/...` provenance markers.

## Full mode

Store runtime credentials in `.env` or the deployment platform's secret manager. Never put credentials in browser JavaScript, Dify exports, screenshots, logs or issue reports.

Before publishing changes, run:

```bash
python scripts/verify_public_repository.py
```

To report a security issue, contact the repository owner privately instead of opening a public issue containing credentials or user data.
