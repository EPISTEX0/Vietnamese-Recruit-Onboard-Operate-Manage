<!--
  Thank you for contributing to Vroom HR!
  Please complete every section below. Remove or adjust placeholders.
  Refer to CONTRIBUTING.md for conventions.
-->

## Summary of Changes

<!--
  Concise description of what this PR does and why.
  Example: Adds a POST /api/attendance/{id}/check-out endpoint that rejects
  double check-out and records the exit time.
-->

...

## Type of Change

<!-- Check the box that applies (replace the space with an x). One PR focused on one type. -->

- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring
- [ ] Documentation
- [ ] Other (specify below)

## Linked Issue(s)

<!-- Link the GitHub issue(s) this PR resolves, e.g. "Closes #123". -->

- Closes #

## Verification Performed

<!--
  State exactly what you ran and the result. Be specific:
  - Tests: `pytest tests/ -v`, `pnpm test`, etc. (paste the summary line)
  - Lint/type: `ruff check`, `mypy src/`, `pnpm lint`, `tsc` — clean?
  - Manual verification: what did you exercise in the running app?
  If you did not run a check, say so explicitly rather than leaving it implied.
-->

- [ ] Backend: `ruff check src/ tests/` pass
- [ ] Backend: `mypy src/` pass
- [ ] Backend: `pytest tests/ -v` pass
- [ ] Frontend: `pnpm lint` pass
- [ ] Frontend: `pnpm build` pass
- [ ] Frontend: `pnpm test` pass
- [ ] Manual verification performed (describe below)

**Manual verification details:** (if applicable)

...

## Checklist

- [ ] My code follows the project's coding standards and conventions
- [ ] I have read and followed `CONTEXT.md` — domain terms are used consistently
- [ ] Tests have been written or updated to cover the change
- [ ] Documentation has been updated where applicable (`README.md`, `AGENTS.md`, etc.)
- [ ] If architectural: an ADR has been created under `docs/adr/` and referenced here (see `CONTRIBUTING.md`), or a clear reason is given why no ADR is required
- [ ] No secrets, API keys, or personal data are included
- [ ] I have run the formatting/lint/type checks listed above

## Additional Context

<!-- Any extra context for reviewers, screenshots, related PRs, or migration notes. -->

...