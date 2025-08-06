import json
import boto3
from pathlib import Path

# ---------- 1. Configure ---------- #
RUNTIME_ARN = (
    "arn:aws:bedrock-agentcore:us-east-1:132260253285:runtime/flash_news_strands_agent-FK6eTM3ZnR"
)
JSON_PATH = Path("sample_news.json")  # the big JSON you pasted earlier
REGION = "us-east-1"

agent_core = boto3.client("bedrock-agentcore", region_name=REGION)

with JSON_PATH.open("r", encoding="utf-8") as f:
    sample = json.load(f)

headlines = [art["title"] for art in sample["articles"]]

for idx, title in enumerate(headlines, start=1):
    print(f"\n[{idx}/{len(headlines)}] Testing: {title}")

    payload_bytes = json.dumps({"headline": title}).encode("utf-8")

    rsp = agent_core.invoke_agent_runtime(
        agentRuntimeArn=RUNTIME_ARN,
        payload=payload_bytes
    )

    # Check HTTP status (optional)
    assert rsp["statusCode"] == 200  # or rsp["ResponseMetadata"]["HTTPStatusCode"]

    # Drain the StreamingBody <— this gives you bytes
    body_bytes = rsp["response"].read()
    # (after .read(), the stream is exhausted; you can close it if you like)
    # rsp["response"].close()

    # Decode → parse JSON
    result = json.loads(body_bytes.decode("utf-8"))
    print(result)  # {'headline': '…', 'digest': '…'}

    # ④ Grab any diagnostics you need
    session_id = rsp["runtimeSessionId"]
    trace_id = rsp["traceId"]
    print("session:", session_id, "trace:", trace_id)
