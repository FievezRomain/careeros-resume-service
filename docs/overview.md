# CareerOS Resume Service â€” service overview

## Responsibility

Generate and version tailored PDF/DOCX resumes from verified profile facts.

## Data ownership

Resume templates, document versions and generation provenance.

## Interfaces

- Inputs: Verified profile data, job context and user-approved instructions.
- Outputs: Versioned documents for ATS validation.
- Dependencies: profile, job and AI gateway services.

## Resume version registry

- `POST /v1/resumes/{resume_id}/versions` requires `resume:versions:write` and a
  UUID `Idempotency-Key`. First registration returns 201 with a location and
  version. An identical retry returns 200 with the original version. Different
  metadata under the same owner, resume and key returns 409.
- `GET /v1/resumes/{resume_id}/versions` lists owned versions in chronological
  version order with `after` and `limit`. The optional `nextAfter` cursor
  continues the list. `GET /v1/resumes/{resume_id}/versions/{number}` reads a
  single version. Both reads require `resume:versions:read` and return 404 for
  missing or differently owned records.
- Each version records a profile snapshot UUID, optional opportunity version
  UUID, creation time and one or two artifact descriptors. Each descriptor
  includes the PDF or DOCX format, an opaque object UUID, byte size and SHA-256
  digest. An authorized writer supplies the metadata. Generation, binary storage
  integration and approval use separate workflows.
- PostgreSQL assigns immutable version numbers per tenant, account and resume
  in a transaction. Startup applies and verifies the adapter-owned schema before
  readiness. Live and ready endpoints are available under `/health`.

## Grounded resume drafts

`POST /v1/resume-drafts` accepts `profileSnapshotId`, optional
`opportunityVersionId` and optional `factIds`. It requires
`resume:drafts:generate` and `profile:read`. A targeted draft also requires
`jobs:read` and an owned opportunity version with full text.

The service reads the versioned source APIs with the caller's credential.
It copies confirmed experience, education and skill values into structured
entries, each with a `sourceFactId`. Without a selection it includes all confirmed
facts. The response pins both source versions when an opportunity is selected.

A missing or differently owned source returns 404. Unavailable fact selections,
empty confirmed content and link-only opportunities return 422. Invalid source
responses return 502, while source outages or missing source configuration
return 503. Configure `PROFILE_API_BASE_URL` and, for targeted drafts,
`JOB_API_BASE_URL`. Drafts are generated on demand for later rendering.

## Contract rules

- APIs and events are versioned.
- Consumers must be idempotent where retries are possible.
- Correlation identifiers are propagated without logging sensitive payloads.
- Authorization and ownership are checked at the service boundary.

## Non-goals

This service does not absorb responsibilities owned by another CareerOS service. Cross-service workflows are coordinated through explicit contracts rather than shared databases.

## Security and privacy

Persist the minimum data required, record provenance, define retention, redact logs and use synthetic examples in this public repository.
