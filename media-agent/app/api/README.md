# API

`api` contains FastAPI support code rather than business behavior.

- `auth.py`: request IDs, bearer-token verification, and error responses.
- `dependencies.py`: shared HTTP client lifecycle helpers.
- `responses.py`: response envelope and status-code mapping.

Route functions are composed in `app/main.py`.
