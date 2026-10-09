# Changelog

All notable changes to SwasthSetu are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-10-09

### Added

- **`/api/version` endpoint** — lightweight version probe for deploy verification and CI
  checks; returns app name, version, environment and Python version without touching the
  database.
- **Architecture diagram** in README — a Mermaid flow diagram showing the request path from
  facility report through trust scoring, forecasting, early warning, and redistribution.
- **Deploy status badge** in README header.
- This `CHANGELOG.md`.

### Changed

- README gains a visual architecture overview alongside the existing table.

## [0.1.0] — 2026-09-20

### Added

- Initial public prototype: national stock map, federated demand forecasting (Flower +
  PyTorch LSTM across four state silos), outbreak-driven early warnings (NCDC IDSP data),
  OR-Tools redistribution solver, Gemini vision for ward boards and stock documents,
  trust scoring, two-sided medicine ledger, role-based access control, SMS/WhatsApp/IVR
  simulator, pharmacist workspace with daily briefings in English/Hindi.
- 891 unit tests, 13 integration check suites.
- Deployed on Render with synthetic data for 3,510 facilities across 157 districts.
