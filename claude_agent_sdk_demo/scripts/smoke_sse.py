from __future__ import annotations

import json
import os
import urllib.request


payload = json.dumps(
    {
        "question": "用一句话说明这个流式 harness 是否工作。",
        "skill_names": ["financial-research"],
        "enable_web_search": True,
    },
    ensure_ascii=False,
).encode("utf-8")
headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
if os.getenv("DEMO_CLIENT_API_KEY"):
    headers["X-Demo-Api-Key"] = os.environ["DEMO_CLIENT_API_KEY"]
request = urllib.request.Request(
    os.getenv("DEMO_STREAM_URL", "http://127.0.0.1:8008/v1/runs/stream"),
    data=payload,
    headers=headers,
    method="POST",
)
with urllib.request.urlopen(request, timeout=30) as response:
    for raw_line in response:
        line = raw_line.decode("utf-8").rstrip()
        if line.startswith("data: "):
            event = json.loads(line[6:])
            print(event["seq"], event["type"], json.dumps(event["data"], ensure_ascii=False))
