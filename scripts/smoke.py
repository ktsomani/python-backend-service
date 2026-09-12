"""Exercise the real API -> PostgreSQL -> Beat -> Redis -> Celery path."""

import json
import os
import time
import urllib.request
import uuid

BASE = os.environ.get("API_URL", "http://localhost:8000")


def request(path, body=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


credentials = {"email": f"smoke-{uuid.uuid4()}@example.com", "password": "smoke-test-password-123"}
request("/api/v1/auth/register", credentials)
tokens = request("/api/v1/auth/login", credentials)
headers = {
    "Authorization": "Bearer " + tokens["access_token"],
    "Idempotency-Key": str(uuid.uuid4()),
}
job = request("/api/v1/jobs", {"text": "hello hello world"}, headers)
deadline = time.monotonic() + 90
while time.monotonic() < deadline:
    result = request("/api/v1/jobs/" + job["id"], headers=headers)
    if result["status"] == "completed":
        assert result["result"]["words"] == 3, result
        print("End-to-end report completed successfully")
        break
    time.sleep(2)
else:
    raise SystemExit("Report did not complete within 90 seconds")
