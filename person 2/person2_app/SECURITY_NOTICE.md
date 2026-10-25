# SatyaLens — Authentication and Deployment Security Notice

## Current state

**SatyaLens has NO authentication on any endpoint.**

Every API route (cases, consent, evidence, analysis, decisions, checklist,
feedback) is accessible to any caller who can reach the server. Access control
is limited to case-ID-based scoping — anyone who knows a case ID can:

- Read, modify, or delete any case.
- Upload or delete evidence.
- Record or revoke consent on behalf of another user.
- Request decisions.
- Submit or delete feedback.

This is intentional for a **local, single-user demo prototype**. It is
**NOT safe for public deployment** without the additions listed below.

---

## Minimum requirements for public deployment

Before exposing SatyaLens on a network accessible to multiple users or
untrusted callers, ALL of the following must be implemented:

| Requirement | Status |
|-------------|--------|
| HTTPS / TLS termination | ❌ Not implemented |
| User authentication (e.g. JWT, OAuth 2.0, session cookies) | ❌ Not implemented |
| Per-case ownership enforcement | ❌ Not implemented |
| Rate limiting on upload endpoints | ❌ Not implemented |
| Encryption at rest for evidence files | ❌ Not implemented |
| Input size limits at the network layer (reverse proxy) | ❌ Not implemented (only app-layer) |
| Audit logging of case access | ❌ Not implemented |
| Secure secret management (.env kept out of git) | ✅ .gitignore excludes .env |

---

## Route-by-route access status

| Route | Consent required | Auth required |
|-------|-----------------|---------------|
| `POST /api/cases` | No | No |
| `GET  /api/cases` | No | No |
| `GET  /api/cases/{id}` | No | No |
| `DELETE /api/cases/{id}` | No | No |
| `POST /api/cases/{id}/evidence/consent` | No | No |
| `POST /api/cases/{id}/evidence` | Yes (consent_status=granted) | No |
| `DELETE /api/cases/{id}/evidence/{ev_id}` | No | No |
| `POST /api/cases/{id}/evidence/{ev_id}/analysis` | Yes (consent check in evidence) | No |
| `POST /api/cases/{id}/decision` | Yes (consent_status=granted) | No |
| `GET  /api/cases/{id}/decision` | Yes (consent_status=granted) | No |
| `GET  /api/cases/{id}/checklist` | No | No |
| `PATCH /api/cases/{id}/checklist/{item_id}` | No | No |
| `GET  /api/cases/{id}/feedback` | No | No |
| `PUT  /api/cases/{id}/feedback` | No | No |
| `DELETE /api/cases/{id}/feedback` | No | No |

---

## Why authentication was not added in this task

Adding authentication to an existing API requires:
- Choosing an identity model (local accounts, OAuth provider, etc.)
- Migrating the database schema.
- Updating every route, test, and the frontend.
- Handling token issuance, expiry, and refresh.
- Auditing every error path for information leakage.

Rushing an incomplete authentication implementation would create a false
sense of security. The decision is to keep the app **explicitly documented
as unauthenticated** and **restricted to local/demo use**.

---

## Safe use instructions

1. Run SatyaLens only on `localhost` (default: `127.0.0.1:8000`).
2. Do not bind to `0.0.0.0` or expose via a public IP or cloud service.
3. Do not upload real evidence from live fraud cases in this prototype.
4. Do not share the running port with untrusted network users.
5. Use real case data only after implementing authentication and encryption.
