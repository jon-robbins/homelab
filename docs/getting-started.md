# Getting started

**Status: Current.** This is the supported public path. It validates the
module catalog off-host; it does not deploy or start containers.

## Requirements

Python 3.11+. From a fresh clone:

```bash
cp .env.example .env.local
bash scripts/ci/public-check.sh
```

The script creates disposable synthetic secret files, renders with
the catalog, checks documentation links, runs
boundary/CI/release tests, and scans public-readiness. It never applies
Compose. Do not add credentials to `.env.local`.

The fictional `hello` and `whoami` names used in the architecture example are
illustrative only; they are not services in this stack.
