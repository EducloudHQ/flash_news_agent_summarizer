#!/usr/bin/env python
import argparse, boto3, botocore

parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True)
parser.add_argument("--agent-name", required=True)
parser.add_argument("--role-arn", required=True)
args = parser.parse_args()

ctl = boto3.client("bedrock-agentcore-control")

try:
    # Attempt update in-place
    ctl.update_agent_runtime(
        name=args.agent_name,
        artifact={"containerConfiguration": {"containerUri": args.image}},
        roleArn=args.role_arn
    )
    print("✔︎ Updated existing runtime")
except botocore.exceptions.ClientError as e:
    if e.response["Error"]["Code"] == "ResourceNotFoundException":
        ctl.create_agent_runtime(
            name=args.agent_name,
            artifact={"containerConfiguration": {"containerUri": args.image}},  # :contentReference[oaicite:2]{index=2}
            protocolConfiguration={"serverProtocol": "MCP"},
            roleArn=args.role_arn
        )
        print("✔︎ Created new runtime")
    else:
        raise
