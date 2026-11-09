# Security policy

This repository is under active development. A successful CI run is not a
production security certification or financial approval. Use synthetic inputs
for public bug reports and pull requests.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting form when it is available:
https://github.com/parkwh79-stack/real-estate-os/security/advisories/new

Do not put credentials, private documents, personal information, database
exports, or exploit details in a public issue. If private reporting is not
available, contact the repository owner privately to arrange a secure channel;
do not publish the sensitive material while waiting. No response-time SLA is
currently offered.

## Credential exposure

Revoke or rotate an exposed credential first. Deleting a file or adding it to
`.gitignore` does not remove earlier commits, forks, caches, logs or artifacts.
Coordinate history rewriting with the owner and collaborators; never rewrite
shared history automatically. Keep incident evidence in private storage.

## Deployment boundary

- Keep runtime data and secrets outside Git, client bundles and public logs.
- Use separate migration, API, identity and worker database roles.
- Preserve tenant checks, RLS, evidence review and final-publication gates.
- Missing evidence must remain unresolved; AI output is not financial authority.
- Apply only reviewed, pending migrations; do not reset a hosted database to
  make its migration history look clean.
- Public source code does not grant access to hosted projects or private data.

See [deployment requirements](docs/product/deployment-plan.md) and
[repository invariants](AGENTS.md) before operating a hosted instance.
