# Profit Tracker Dashboard

Auto-built every night by GitHub Actions. The encrypted dashboard is served via GitHub Pages
at `https://<owner>.github.io/<repo>/` and requires the dashboard password.

- `config.json` — product groups, ust factor, active campaigns
- `scripts/` — pipeline (Shopify pull, JSON aggregation, dashboard render)
- `data/` — input snapshots (Shopify pulls, MHTML-extracted Print-Labs costs, manually entered ad spend)
- `.github/workflows/build-dashboard.yml` — the build pipeline
