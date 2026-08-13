# Security Policy

Vroom HR handles sensitive employee data. We take security seriously — please report vulnerabilities responsibly so we can fix them before they are disclosed.

## Supported Versions

The following table lists which versions currently receive security updates. Only the latest released version (and the current `main` branch) receive security patches.

| Version           | Status           |
| ----------------- | ---------------- |
| `main` (unreleased) | ✅ Supported — active development, receives fixes immediately |
| 0.x latest release | ✅ Supported — receives security backports |
| Older 0.x releases | ❌ Not supported |

> **Self-hosted note:** Vroom HR is self-hosted — every deployment is a single-company instance running its own database and server. You are responsible for keeping your deployment updated and for applying security patches promptly.

## Reporting a Vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report security issues **privately** through one of the channels below:

1. **GitHub Private Vulnerability Reporting** *(recommended)*
   - Use the "Report a vulnerability" button on the [Security tab](https://github.com/EPISTEX0/Vietnamese-Recruit-Onboard-Operate-Manage/security) of the repository.

2. **Email**
   - **`security@vroomhr.com`**

We ask that you:

- Provide a clear description of the vulnerability and its impact.
- Include steps to reproduce, affected versions, and (if possible) a proof of concept.
- Allow us a **90-day coordinated disclosure period** from confirmation before publishing any details publicly.
- Do not exploit the vulnerability beyond demonstrating it, and do not access another person's data without permission.

### What to expect

- **Acknowledgment** within **3 business days** of your report.
- **Status updates** at least every **10 business days** until resolution.
- An answer and timeline for the fix, including whether it will be fixed in the current release, a patch release, or the next major release.
- Public acknowledgment after the fix is released, unless you prefer to remain anonymous.

## Data Privacy & Single-Tenant Security Principles

These are structural invariants of the product. Any change that weakens them must be treated as a security regression and require an [ADR](docs/adr/).

### Single-Tenant Deployment & Database Isolation

- **One deployment serves exactly one company (Organization).** Each deployment runs its own PostgreSQL database and its own server. There is **no multi-tenancy** in a deployment — `tenant_id` is treated as an implementation detail to be frozen or removed, not an active isolation boundary.
- Data isolation is enforced at the **deployment boundary**, not inside the application.
- Never design features on the assumption that a single instance holds data for multiple companies.

### PII Redaction in LLM Pipelines

- Any data sent to an external LLM provider **must be redacted** of PII before leaving the deployment, unless explicitly required for the task and governed by the Organization's AI configuration.
- The **AI Evaluation Set** only contains samples that have been **selected, labeled, and redacted** for quality measurement — it is **not** training data and must **never** enable online learning from production data.
- The **AI Assistant** is a human-in-the-loop system: it may **read** and **draft** only — it must **never** write to the database autonomously. Expose **Read-Tools** and **Draft-Tools** only; never expose an LLM a tool that can write.
- The **Employee Assistant** may only read the data of the employee it serves and may only draft employee-owned actions.

### Secret & Key Management

- Never commit secrets, API keys, passwords, or OAuth tokens to the repository. Use `.env` files (excluded via `.gitignore`) or a secrets manager.
- OAuth client secrets and LLM provider API keys are infrastructure secrets managed exclusively by the **System Admin**. HR and Employees must never have access to them.
- JWT signing keys and OAuth token encryption keys are deployment-critical; rotate them on a schedule and never share them across deployments.

### Role Separation

- **System Admin** configures infrastructure and secrets but is **strictly blocked** from business HR data.
- **HR** manages business data (Candidates, Employees, Knowledge Base, Attendance, Payslip) but has **no access** to infrastructure secrets.
- **Employee Self-Service** is limited to the employee's own data and employee-owned actions.

---

## Security Best Practices for Deployers

- Run the latest supported version and apply security patches promptly.
- Use strong, unique secrets for PostgreSQL, Redis, and MinIO; override the Docker Compose defaults in production.
- Serve all traffic over HTTPS (TLS) in production.
- Restrict database/Redis/MinIO ports to private networks; never expose them to the public internet.
- Configure backups and test restore procedures regularly.

## Contact

For all security matters: **`security@vroomhr.com`**