# CareerOS Resume Service â€” service overview

## Responsibility

Generate and version tailored PDF/DOCX resumes from verified profile facts.

## Data ownership

Resume templates, document versions and generation provenance.

## Interfaces

- Inputs: Verified profile data, job context and user-approved instructions.
- Outputs: Versioned documents for ATS validation.
- Dependencies: profile, job and AI gateway services.

## Contract rules

- APIs and events are versioned.
- Consumers must be idempotent where retries are possible.
- Correlation identifiers are propagated without logging sensitive payloads.
- Authorization and ownership are checked at the service boundary.

## Non-goals

This service does not absorb responsibilities owned by another CareerOS service. Cross-service workflows are coordinated through explicit contracts rather than shared databases.

## Security and privacy

Persist the minimum data required, record provenance, define retention, redact logs and use synthetic examples in this public repository.