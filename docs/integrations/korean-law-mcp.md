# Korean Law transport integration

Updated: 2026-07-18

The authoritative architecture is defined in
[`docs/architecture/legal-intelligence-core.md`](../architecture/legal-intelligence-core.md).

## Decision

Korean Law MCP is an optional research transport. It is not a legal source
authority, is not required for normal Legal Pod execution, and cannot clear an
approval gate merely because a tool call succeeded.

The resolution order is:

1. verified PostgreSQL legal snapshot;
2. official Law.go.kr direct API gap fill;
3. optional self-hosted MCP candidate retrieval;
4. optional remote MCP candidate retrieval, only when explicitly enabled.

Direct search-list rows and MCP rows remain discovery candidates. They become
approval-grade evidence only after the official payload has passed the corpus
sync attestation contract and an exact local resource/version/provision
snapshot has been sealed.

Every result keeps these fields independent:

- `sourceAuthority`: who published the material;
- `transport`: how REALYTICS retrieved it;
- `verificationStatus`: what was actually verified;
- `contentHash`, `asOfDate`, effective dates, and exact locator.

A caller-supplied `lawContext` and every unattested MCP result are
`untrusted_candidate`. They may help discovery but cannot become
approval-grade evidence without verification against an official source or a
verified local snapshot.

## Configuration

```env
# Official direct fallback
LAW_OC=
REALYTICS_KOREAN_LAW_API_URL=https://www.law.go.kr/DRF/lawSearch.do

# Optional internal sidecar
REALYTICS_LEGAL_MCP_LOCAL_URL=http://127.0.0.1:8787/tools/call
# DNS hostnames other than localhost must be explicitly allowlisted and use
# HTTPS. Loopback/private IP literals may use HTTP inside the trusted network.
REALYTICS_LEGAL_MCP_LOCAL_ALLOWED_HOSTS=

# Legacy alias accepted during migration
REALYTICS_KOREAN_LAW_MCP_URL=

# Remote MCP remains disabled by default
REALYTICS_LEGAL_MCP_REMOTE_URL=
REALYTICS_LEGAL_REMOTE_MCP_ENABLED=false
```

`LAW_OC` and any MCP credential must come from the deployment secret manager.
They must not be committed, stored in evidence payloads, or emitted in URLs,
errors, logs, or traces.

## Security rules

- The API server does not treat MCP as `sourceAuthority`.
- Remote MCP is fail-closed and must not receive project-confidential or
  legally privileged data.
- A configured “local” MCP is trusted only when it uses localhost/a private IP
  literal, or an explicitly allowlisted HTTPS hostname. Provider redirects are
  rejected before the redirect target is contacted.
- Self-hosting changes the transport trust boundary, not the authority of the
  returned legal material.
- HTTP 200, `connected=true`, and a model-generated citation are not evidence
  verification.
- Official text, AI summary, and human/counsel opinion stay in separate
  content classes.
- Stale or conflicting evidence can be displayed with a warning but cannot
  clear the Legal Approval Readiness gate.

## Runtime behavior

The compatibility function
`integrations.korean_law_mcp.run_korean_law_mcp` now delegates to the Legal
Intelligence Core. Existing callers still receive a `lawContext`-shaped
projection, but:

- `connected=true` means current hash-bound official evidence exists;
- `mcp` fields are compatibility metadata only;
- caller flags are ignored as authority claims;
- provider failures fall through to the next approved provider;
- missing configuration returns an explicit diagnostic and no fabricated
  reference.

The global `GET /legal/search` route is local/trusted-internal by default.
External official or remote-provider fallback is enabled only when the caller
sets `public_query_acknowledged=true`, thereby affirming that the query contains
public-only information. Project Legal Pod queries never use that
acknowledgement and remain `PROJECT_CONFIDENTIAL`.

## Approval contract

Legal evidence passes only when:

- `evidenceStatus` is verified;
- at least one verified reference exists;
- evidence is not stale;
- no source/citation conflict remains open;
- exact source/version locator and content hash are present.

The Legal Pod output remains `analysisOnly=true` and
`reportEligible=false` until an authorized legal reviewer or counsel records a
human decision. The final legal conclusion is never delegated to MCP or an LLM.

## Operations

An MCP outage must not interrupt verified local search or official direct
lookup. On suspected compromise:

1. disable the MCP URL/feature flag;
2. preserve call audit records;
3. quarantine unattested results;
4. verify the pinned image/SBOM and credentials;
5. rerun provider contract and citation tests before restoration.

Official corpus maintenance is a separate worker path. It uses
`REALYTICS_LEGAL_SYNC_DATABASE_URL`, a versioned/Object-Locked S3 or MinIO
bucket, fenced provider/scope leases, checkpoint CAS, and a DB-verified terminal
completion attestation. It never reuses the serving API DSN. Search-list or MCP
payloads remain candidates until a resource-family adapter resolves and
verifies the exact official source payload.

Provisioning, reconciliation, monitoring, and disaster-recovery procedures are
defined in
[`docs/engineering/legal-corpus-operations.md`](../engineering/legal-corpus-operations.md).
