import json

from scripts.release.reconcile import check_sign


def test_sign_check_requires_pinned_cosign_and_oidc(monkeypatch):
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://example.test/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "test-token")

    class Result:
        returncode = 0
        stdout = '{"gitVersion":"v2.2.3"}'
        stderr = ""

    monkeypatch.setattr("scripts.release.reconcile.subprocess.run", lambda *args, **kwargs: Result())

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"value": "oidc-token"}).encode()

    monkeypatch.setattr("scripts.release.reconcile.urllib.request.urlopen", lambda *args, **kwargs: Response())
    assert all(check_sign())


def test_sign_check_rejects_cosign_version_near_match(monkeypatch):
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", "https://example.test/token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "test-token")

    class Result:
        returncode = 0
        stdout = '{"gitVersion":"v2.2.30"}'
        stderr = ""

    monkeypatch.setattr("scripts.release.reconcile.subprocess.run", lambda *args, **kwargs: Result())

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"value": "oidc-token"}).encode()

    monkeypatch.setattr("scripts.release.reconcile.urllib.request.urlopen", lambda *args, **kwargs: Response())
    assert check_sign() == [False, True]
