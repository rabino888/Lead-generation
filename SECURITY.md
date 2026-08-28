# Security Policy

## Supported versions

Security fixes are applied on the `main` branch. This project is operator-run tooling — pin your deploy to a known commit when running in production.

## Reporting a vulnerability

**Do not** open a public GitHub issue for security-sensitive reports.

Email the maintainer with:

- Description of the issue and impact
- Steps to reproduce
- Whether you believe secrets or client PII may be exposed

We will acknowledge within a few business days and coordinate a fix before public disclosure when appropriate.

## Secrets and client data

- Store API keys in `.env` or your host’s secret manager — never in git.
- `data/` contains campaign seeds, enriched leads, and cost logs. It is **gitignored** by design.
- Google service account JSON must not be committed. Use `GOOGLE_SERVICE_ACCOUNT` as a single-line env var (see `.env.example`).
- The FastAPI `/run` endpoint is **not authenticated** in the current codebase. Do not expose it publicly without adding auth and network controls.

## Third-party services

This agent calls Apollo, Firecrawl, Apify, LLM providers, and Google APIs. Review each vendor’s data handling and terms before processing regulated or sensitive prospect data.
