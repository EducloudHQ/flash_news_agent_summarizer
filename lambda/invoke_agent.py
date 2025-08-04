import os
import json
import uuid
import boto3
from base64 import b64decode

# ---------- clients ----------
agent_core = boto3.client("bedrock-agentcore")  # InvokeAgentRuntime
sns = boto3.client("sns")

AGENT_ARN = os.environ["AGENT_ARN"]  # runtime ARN created by agentcore CLI
TOPIC_ARN = os.environ["TOPIC_ARN"]  # SNS fan-out topic


def main(event, context):
    """
    Lambda ⇢ triggered by Kinesis stream.
    For each record: decode → call AgentCore runtime → publish digest to SNS.
    """
    # Fresh trace-id per batch (optional but keeps <128-byte limit)
    trace_id = f"Root=1-{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:24]}"

    for rec in event["Records"]:
        # 1️⃣  Extract headline from the Kinesis payload
        raw_json = b64decode(rec["kinesis"]["data"]).decode()
        headline = json.loads(raw_json)["headline"]
        print("Headline:", headline)

        # 2️⃣  Invoke the Flash-News agent
        payload_bytes = json.dumps({"headline": headline}).encode("utf-8")

        rsp = agent_core.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            payload=payload_bytes,
            traceId=trace_id  # must be ≤128 chars
        )

        body_bytes = rsp["response"].read()
        outer = json.loads(body_bytes.decode("utf-8"))

        # The agent puts its answer in the first content block’s text field
        inner_json_str = outer["content"][0]["text"]
        inner = json.loads(inner_json_str)  # {'headline': '...', 'digest': '...'}

        digest = inner["digest"]

        print(digest)

        # 3️⃣  Publish to SNS
        sns.publish(
            TopicArn=TOPIC_ARN,
            Message=f"{digest}\n({headline})"
        )
        print("Published to SNS")
