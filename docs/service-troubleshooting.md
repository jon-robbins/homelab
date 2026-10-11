# Service troubleshooting

**Status: Current public checks.** First run:

```bash
bash scripts/ci/public-check.sh
```

If Compose rendering fails, check that Docker Compose v2 is installed and that
`.env.example` is unchanged. If a shell test fails, run the named script from
`tests/compose/` directly. If a runtime service is unhealthy, runtime logs,
storage, credentials, and ingress are private deployment concerns not
reproducible from this public checkout.

A host controller and automatic remediation are **Planned**.

