# Service troubleshooting

**Status: Current public checks.** First run:

```bash
bash scripts/ci/public-check.sh
```

If module rendering fails, run the catalog tests and inspect the reported
module. If a runtime service is unhealthy, runtime logs,
storage, credentials, and ingress are private deployment concerns not
reproducible from this public checkout.

A host controller and automatic remediation are **Planned**.
