# Profit Tracker Dashboard

Auto-built every night by GitHub Actions. The encrypted dashboard is served via GitHub Pages
at `https://<owner>.github.io/<repo>/` and requires the dashboard password.

- `config.json` — product groups, ust factor, active campaigns
- Neue Kategorien lassen sich im Dashboard unter „Ad Spend eintragen → + Neue Kampagne → + Neue Kategorie…“ anlegen.
  Sie landen in `data/ad_spend_user.json` unter `groups` und werden beim Build hinter die Gruppen aus `config.json` gehängt.
- `scripts/` — pipeline (Shopify pull, JSON aggregation, dashboard render)
- `data/` — input snapshots (Shopify pulls, MHTML-extracted Print-Labs costs, manually entered ad spend)
- `.github/workflows/build-dashboard.yml` — the build pipeline
