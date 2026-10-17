# Getting started

**Status: Current.** This is the supported public check path. It does not
deploy or start containers.

## Requirements

Python 3.11+. From a fresh clone:

```bash
cp .env.example .env.local
bash scripts/ci/public-check.sh
```

The script checks documentation links, runs boundary, CI, and release tests,
and scans public-readiness. It never applies Compose. Do not add credentials
to `.env.local`.

The fictional `hello` and `whoami` names used in the architecture example are
illustrative only; they are not services in this stack.
