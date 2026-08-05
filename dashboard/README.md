# AegisOps AI — Dashboard

Frontend-only Streamlit dashboard for the AegisOps AI cloud infrastructure
investigation platform. This app never talks to AWS directly — it reads
JSON that the backend (running on a separate EC2 instance) publishes, via
a swappable `DataSource` (local files today, S3 initially in production,
a REST API later). See `services/CONTRACT.md` for the exact JSON contract.

## Run locally

```bash
cd dashboard
python3 -m venv ../.venv        # if not already created
../.venv/bin/pip install -r requirements.txt
../.venv/bin/streamlit run app.py
```

On first run it defaults to **Local Files** mode, reading the fixtures in
`assets/sample_data/`. Those fixtures exist only so the UI can be developed
and demoed without a live backend — they are read exclusively through the
same `DataSource`/service layer real backend data would flow through, so
switching sources doesn't touch any UI code.

## Switching data source

Two ways, same effect (config resolves env vars → `assets/config/settings.json`):

- **Settings page** in the running app — pick Local / S3 / REST, fill in the
  fields, Save. Persists to `assets/config/settings.json`.
- **Environment variables** (useful for the production EC2 deployment):

  ```bash
  export AEGISOPS_DATA_SOURCE=s3
  export AEGISOPS_S3_BUCKET=aegisops-reports
  export AEGISOPS_S3_PREFIX=aegisops/
  export AEGISOPS_AWS_REGION=us-east-1
  ```

  AWS credentials are resolved via the standard boto3 chain (instance role,
  env vars, `~/.aws/credentials`) — nothing is hardcoded here.

## Project layout

```
app.py                  entrypoint: config -> services -> active page
components/              all UI: theme, cards, charts, tables, badges, sidebar...
components/views/        one module per page (Overview, Investigation, History, Analytics, Settings)
services/                DataSource abstraction + SummaryService/CollectorService/
                         HistoryService/ReportService/AnalyticsService + config
services/CONTRACT.md     the JSON contract every service expects — read this first
                         when wiring up the real backend
styles/theme.css         the entire dark enterprise theme
assets/sample_data/      local-dev-only JSON fixtures (see above)
assets/config/           persisted runtime settings (git-ignore in production)
```

## For the backend team

Publish JSON matching `services/CONTRACT.md` to the configured S3
prefix (or local directory during integration testing) — five objects:
`summary.json`, `collectors.json`, `executions.json`, `reports.json`,
`analytics.json`. No dashboard code changes should be required.
