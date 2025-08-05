#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time

import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime


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

    print(f"→ Launching runtime (kwargs={launch_kwargs or 'default'})")
    runtime.launch(**launch_kwargs)

    # Wait for terminal state
    terminal = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    runtime_arn = None
    while True:
        status_resp = runtime.status()
        endpoint = None
        if hasattr(status_resp, "endpoint"):
            endpoint = status_resp.endpoint
        elif isinstance(status_resp, "dict"):
            endpoint = status_resp.get("endpoint")

        status = endpoint.get("status") if isinstance(endpoint, dict) else None
        runtime_arn = (
            endpoint.get("arn") if isinstance(endpoint, dict) else runtime_arn
        ) or (
            endpoint.get("endpointArn") if isinstance(endpoint, dict) else runtime_arn
        )

        print(f"Agent status: {status}")
        if status in terminal:
            break
        time.sleep(10)

    # Persist ARN if requested (fallback to YAML if status didn't include it)
    if args.ssm_param and not runtime_arn:
        try:
            cfg_path = os.path.join(os.getcwd(), ".bedrock_agentcore.yaml")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    for line in f:
                        s = line.strip()
                        if s.startswith(("endpointArn:", "runtimeArn:")):
                            runtime_arn = s.split(":", 1)[1].strip()
                            print(f"ℹ︎ Fallback ARN from YAML: {runtime_arn}")
                            break
        except Exception as e:
            print(f"⚠️ Could not read ARN from .bedrock_agentcore.yaml: {e}", file=sys.stderr)

    if args.ssm_param and runtime_arn:
        boto3.client("ssm").put_parameter(
            Name=args.ssm_param, Value=runtime_arn, Type="String", Overwrite=True
        )
        print(f"✔︎ Stored runtime ARN in SSM: {runtime_arn}")
    elif not runtime_arn:
        print("⚠️ Could not determine runtime ARN; skip SSM write.", file=sys.stderr)


if __name__ == "__main__":
    main()
