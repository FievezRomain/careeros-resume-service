# CareerOS Resume Service

Generate and version tailored PDF/DOCX resumes from verified profile facts.

## Scope

- **Owns:** Resume templates, document versions and generation provenance.
- **Inputs:** Verified profile data, job context and user-approved instructions.
- **Outputs:** Versioned documents for ATS validation.
- **Dependencies:** profile, job and AI gateway services.

## Safety baseline

This public repository must never contain secrets, real resumes, real applications or personal data. External communication and application submission always require explicit user approval.

## Documentation

See [docs/overview.md](docs/overview.md) and the central [CareerOS workspace](https://github.com/FievezRomain/careeros-workspace).

## Local development

Configure the OIDC settings and the service-owned PostgreSQL database using
`.env.example`. The V1-027 API exposes a version and artifact metadata registry.

```text
uv sync --locked --all-groups
uv run uvicorn careeros_resume.bootstrap:create --factory --no-access-log
uv run pytest
```

## License

Apache-2.0.
