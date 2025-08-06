#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time

import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime
import botocore.exceptions as exc


def main():
    p = argparse.ArgumentParser(description="Configure & launch Bedrock AgentCore runtime (toolkit-driven).")
    p.add_argument("--agent-name", required=True, help="AgentCore runtime name")
    p.add_argument("--role-arn",   required=True, help="Execution role ARN (from CDK)")
    p.add_argument("--region",     default=os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION"))
    p.add_argument("--workdir",    default=os.getenv("WORKDIR", "strands"),
                   help="Folder that contains entrypoint and requirements.")
    p.add_argument("--entrypoint", default=os.getenv("ENTRYPOINT", "flash_news_agent.py"))
    p.add_argument("--requirements", default=os.getenv("REQUIREMENTS_FILE", "requirements.txt"))
    p.add_argument("--auto-create-ecr", action="store_true", default=True)
    p.add_argument("--no-auto-create-ecr", dest="auto_create_ecr", action="store_false")
    p.add_argument("--local", action="store_true", help="Run locally (dev).")
    p.add_argument("--local-build", action="store_true", help="Build here in CodeBuild then deploy.")
    p.add_argument("--auto-update", action="store_true", default=True,
                   help="If the agent already exists, update it instead of failing.")
    p.add_argument("--no-auto-update", dest="auto_update", action="store_false")
    p.add_argument("--ssm-param", default=os.getenv("AGENT_ARN_PARAM", "/agentcore/flash-news/runtime-arn"),
                   help="SSM parameter to store runtime ARN (leave empty to skip).")
    args = p.parse_args()

    # Change into the code directory
    if args.workdir and args.workdir != ".":
        if not os.path.isdir(args.workdir):
            print(f"❌ workdir '{args.workdir}' not found", file=sys.stderr)
            sys.exit(1)
        os.chdir(args.workdir)

    # Region resolution
    sess = Session()
    region = args.region or (sess.region_name or "us-east-1")

    runtime = Runtime()

    # Configure (toolkit handles Dockerfile/ECR/build config)
    cfg = runtime.configure(
        entrypoint=args.entrypoint,
        execution_role=args.role_arn,
        auto_create_ecr=bool(args.auto_create_ecr),
        requirements_file=args.requirements,
        region=region,
        agent_name=args.agent_name,
    )
    print("Configure response:")
    print(json.dumps(cfg, indent=2, default=str))

    # 🔧 Scrub cached runtime/agent identifiers so we don't try to update a missing one
    try:
        cfg_path = os.path.join(os.getcwd(), ".bedrock_agentcore.yaml")
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                original = f.readlines()
            filtered = []
            for ln in original:
                s = ln.strip()
                if s.startswith((
                    "endpointArn:", "endpoint_arn:",
                    "runtimeArn:", "runtime_arn:",
                    "runtimeId:", "agentId:", "agent_id:",
                )):
                    continue
                filtered.append(ln)
            if filtered != original:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.writelines(filtered)
                print("🔧 Cleared cached runtime/agent identifiers in .bedrock_agentcore.yaml")
    except Exception as e:
        print(f"⚠️ Failed to scrub .bedrock_agentcore.yaml: {e}")

    # Launch
    launch_kwargs = {}
    if args.local:
        launch_kwargs["local"] = True
    if args.local_build:
        launch_kwargs["local_build"] = True
    if args.auto_update:
        # New: let the toolkit update if the agent already exists
        launch_kwargs["auto_update_on_conflict"] = True

    print(f"→ Launching runtime (kwargs={launch_kwargs or 'default'})")
    try:
        launch_response = runtime.launch(**launch_kwargs)
        print("Launch response:", json.dumps(launch_response, default=str, indent=2))
    except exc.ClientError as e:
        # Fallback: if toolkit version doesn’t accept the kwarg or we still got a conflict, retry with update
        msg = str(e)
        if "ConflictException" in msg or "already exists" in msg:
            print("ℹ︎ Conflict detected; retrying with auto-update-on-conflict...")
            launch_response = runtime.launch(auto_update_on_conflict=True, **{k: v for k, v in launch_kwargs.items() if k != "auto_update_on_conflict"})
            print("Launch response:", json.dumps(launch_response, default=str, indent=2))
        else:
            raise
    # Extract agent ARN directly from launch response
    agent_arn = launch_response.get('agent_arn')
    if not agent_arn:
        print("❌ Launch response did not include agent_arn", file=sys.stderr)
        sys.exit(4)
    agent_arn = launch_response.get('agent_arn')
    if not agent_arn:
        print("❌ Launch response did not include agent_arn", file=sys.stderr)
        sys.exit(4)

    # Wait for terminal state
    terminal = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    runtime_arn = None
    while True:
        status_resp = runtime.status()
        endpoint = None
        if hasattr(status_resp, "endpoint"):
            endpoint = status_resp.endpoint
        elif isinstance(status_resp, dict):  # fixed type check
            endpoint = status_resp.get("endpoint")

        status = endpoint.get("status") if isinstance(endpoint, dict) else None


        print(f"Agent status: {status}")
        if status in terminal:
            break
        time.sleep(10)

    # Persist ARN if requested
    if agent_arn:
        boto3.client("ssm").put_parameter(
            Name=args.ssm_param, Value=agent_arn, Type="String", Overwrite=True
        )
        print(f"✔︎ Stored runtime ARN in SSM: {runtime_arn}")
    elif not runtime_arn:
        print("⚠️ Could not determine runtime ARN; skip SSM write.", file=sys.stderr)


if __name__ == "__main__":
    main()
