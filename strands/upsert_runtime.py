#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time

import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime
import yaml
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

    # Resolve region
    sess = Session()
    region = args.region or (sess.region_name or "us-east-1")

    runtime = Runtime()

    # Configure
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

    # Clean cached runtime IDs / force the name to what we pass
    def _scrub_yaml_ids(cfg_path: str, desired_name: str) -> None:
        """Safely load and rewrite the toolkit YAML to normalize name and drop cached IDs/ARNs."""
        try:
            if not os.path.exists(cfg_path):
                return
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Normalize agent name across common keys
            for k in ("name", "agentName", "agent_name"):
                data[k] = desired_name

            # Drop cached identifiers at top-level
            for k in (
                "endpointArn", "endpoint_arn",
                "runtimeArn",  "runtime_arn",
                "runtimeId",
                "agentId",     "agent_id",
            ):
                data.pop(k, None)

            # If an 'endpoint' block exists, strip volatile fields
            ep = data.get("endpoint")
            if isinstance(ep, dict):
                for k in ("arn", "endpointArn", "endpoint_arn", "status"):
                    ep.pop(k, None)
                if not ep:
                    data.pop("endpoint", None)

            with open(cfg_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
            print("🔧 Scrubbed cached identifiers and normalized name in .bedrock_agentcore.yaml")
        except Exception as e:
            print(f"⚠️ Failed to scrub/normalize .bedrock_agentcore.yaml: {e}", file=sys.stderr)

    cfg_path = os.path.join(os.getcwd(), ".bedrock_agentcore.yaml")
    _scrub_yaml_ids(cfg_path, args.agent_name)

    # Build and upsert (single upsert with auto-update enabled)
    launch_kwargs = {}
    if args.local:
        launch_kwargs["local"] = True
    if args.local_build:
        launch_kwargs["local_build"] = True
    if args.auto_update:
        launch_kwargs["auto_update_on_conflict"] = True

    print(f"→ Launching runtime (kwargs={launch_kwargs or 'default'})")
    # Single call: toolkit handles create-or-update
    try:
        launch_response = runtime.launch(**launch_kwargs)
        # Pretty-print pydantic model (v1: .dict, v2: .model_dump), else repr
        try:
            print("Launch response:", json.dumps(launch_response.dict(), default=str, indent=2))
        except Exception:
            try:
                print("Launch response:", json.dumps(launch_response.model_dump(), default=str, indent=2))
            except Exception:
                print("Launch response (repr):", launch_response)
    except exc.ClientError as e:
        # If the toolkit tries to update a stale agent-id (ResourceNotFound), purge cache & retry
        msg = str(e)
        if "ResourceNotFoundException" in msg and "UpdateAgentRuntime" in msg:
            print("ℹ︎ Detected stale agent-id during update; scrubbing YAML and retrying create/update once...")
            _scrub_yaml_ids(cfg_path, args.agent_name)
            # Hard purge: delete the YAML to force a clean create-or-update path
            try:
                if os.path.exists(cfg_path):
                    os.remove(cfg_path)
                    print("🗑️ Deleted cached .bedrock_agentcore.yaml (hard purge)")
            except OSError as purge_err:
                print(f"⚠️ Failed to delete {cfg_path}: {purge_err}", file=sys.stderr)

            # Re-init runtime and re-configure to avoid any in-memory cache
            runtime = Runtime()
            cfg = runtime.configure(
                entrypoint=args.entrypoint,
                execution_role=args.role_arn,
                auto_create_ecr=bool(args.auto_create_ecr),
                requirements_file=args.requirements,
                region=region,
                agent_name=args.agent_name,
            )
            print("🔁 Reconfigured after cache purge")

            launch_response = runtime.launch(**launch_kwargs)
            try:
                print("Launch response (after retry):", json.dumps(launch_response.dict(), default=str, indent=2))
            except Exception:
                try:
                    print("Launch response (after retry):", json.dumps(launch_response.model_dump(), default=str, indent=2))
                except Exception:
                    print("Launch response (after retry) (repr):", launch_response)
        else:
            raise

    # Extract agent ARN directly from launch response
    agent_arn = getattr(launch_response, 'agent_arn', None)
    if agent_arn is None:
        try:
            agent_arn = launch_response.dict().get('agent_arn')
        except Exception:
            try:
                agent_arn = launch_response.model_dump().get('agent_arn')
            except Exception:
                agent_arn = None
    if not agent_arn:
        print("❌ Launch response did not include agent_arn", file=sys.stderr)
        sys.exit(4)
    agent_arn = getattr(launch_response, 'agent_arn', None)
    if agent_arn is None:
        try:
            agent_arn = launch_response.dict().get('agent_arn')
        except Exception:
            try:
                agent_arn = launch_response.model_dump().get('agent_arn')
            except Exception:
                agent_arn = None
    if not agent_arn:
        print("❌ Launch response did not include agent_arn", file=sys.stderr)
        sys.exit(4)

    # Poll until READY
    terminal = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    status = None
    while True:
        resp = runtime.status()
        ep = getattr(resp, 'endpoint', None) or (resp.get('endpoint') if isinstance(resp, dict) else {})
        status = ep.get('status')
        print(f"Agent status: {status}")
        if status in terminal:
            break
        time.sleep(10)

    if status != "READY":
        print(f"❌ Agent not READY (status={status}); aborting SSM write", file=sys.stderr)
        sys.exit(2)

    # Persist runtime ARN to SSM
    if args.ssm_param:
        boto3.client("ssm").put_parameter(
            Name=args.ssm_param,
            Value=agent_arn,
            Type="String",
            Overwrite=True
        )
        print(f"✔︎ Stored runtime ARN in SSM: {agent_arn}")


if __name__ == "__main__":
    main()
