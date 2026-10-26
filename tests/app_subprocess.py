"""Send requests to the real app built under different settings.

`club_server.main` reads its settings once, at import, and the test session has
already imported it with the test environment. A setting that shapes the app
itself (its middleware, or whether it serves the API docs) can only be tested
in a fresh interpreter: this one imports the app with the given environment
and sends the requests through a TestClient. The lifespan does not run, so
nothing here touches the database.
"""

import json
import os
import subprocess
import sys
from typing import TypedDict

_PROBE = """
import json, sys
from fastapi.testclient import TestClient
from club_server.main import app

client = TestClient(app)
out = []
for method, path, headers in json.loads(sys.argv[1]):
    r = client.request(method, path, headers=headers)
    out.append({"status": r.status_code, "headers": {k.lower(): v for k, v in r.headers.items()}})
print(json.dumps(out))
"""


class ProbeRequest(TypedDict):
    method: str
    path: str
    headers: dict[str, str]


class ProbeResponse(TypedDict):
    status: int
    headers: dict[str, str]


def request_app(
    env: dict[str, str | None], requests: list[ProbeRequest]
) -> list[ProbeResponse]:
    """Answer each request from the app imported with ``env`` applied.

    A value of None removes that variable from the environment.
    """
    child_env = dict(os.environ)
    for key, value in env.items():
        if value is None:
            _ = child_env.pop(key, None)
        else:
            child_env[key] = value
    args = json.dumps([[r["method"], r["path"], r["headers"]] for r in requests])
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, args],
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    responses: list[ProbeResponse] = json.loads(done.stdout.strip().splitlines()[-1])
    return responses
