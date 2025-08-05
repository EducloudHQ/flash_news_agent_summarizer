#!/usr/bin/env python3
"""
Upsert a Bedrock AgentCore runtime and persist its ARN in SSM.

Usage:
  python upsert_runtime.py \
      --image 123456789012.dkr.ecr.us-east-1.amazonaws.com/flash-news:abcdef \
      --agent-name flash_news_strands_agent \
      --role-arn arn:aws:iam::123456789012:role/agentcore-flash_news_strands_agent-role
"""

import argparse
import boto3
import botocore.exceptions as exc
import sys

# ────────────────────────── CLI args ──────────────────────────
ap = argparse.ArgumentParser()
ap.add_argument("--image",       required=True, help="ECR image URI (linux/arm64)")
ap.add_argument("--agent-name",  required=True, help="AgentCore runtime name")
ap.add_argument("--role-arn",    required=True, help="IAM role for the runtime")
ap.add_argument("--ssm-param",   default="/agentcore/flash-news/runtime-arn",
               help="SSM Parameter path that stores the runtime ARN")
args = ap.parse_args()

# ────────────────────────── AWS clients ───────────────────────
ctl = boto3.client("bedrock-agentcore-control")
ssm = boto3.client("ssm")

def put_runtime_arn(arn: str) -> None:
    """Persist runtime ARN in SSM Parameter Store (String)."""
    ssm.put_parameter(Name=args.ssm_param, Value=arn, Type="String", Overwrite=True)
    print(f"✔︎ Stored runtime ARN in SSM: {arn}")

def get_runtime_arn() -> str:
    """Describe the runtime and return its ARN (or raise if not found)."""
    resp = ctl.describe_agent_runtime(name=args.agent_name)
    return resp["runtimeArn"]

# ────────────────────────── Upsert logic ──────────────────────
try:
    # Attempt in-place update
    ctl.update_agent_runtime(
        name=args.agent_name,
        artifact={"containerConfiguration": {"containerUri": args.image}},
        roleArn=args.role_arn,
    )
    arn = get_runtime_arn()
    put_runtime_arn(arn)
    print("✔︎ Updated existing runtime")

except exc.ClientError as err:
    if err.response["Error"]["Code"] != "ResourceNotFoundException":
        # Any error other than "runtime does not exist" → fail hard
        print(f"✖︎ AWS error: {err}", file=sys.stderr)
        raise

    # Runtime not found → create a new one
    resp = ctl.create_agent_runtime(
        name=args.agent_name,
        artifact={"containerConfiguration": {"containerUri": args.image}},
        protocolConfiguration={"serverProtocol": "MCP"},
        roleArn=args.role_arn,
    )
    arn = resp["runtimeArn"]
    put_runtime_arn(arn)
    print("✔︎ Created new runtime")
