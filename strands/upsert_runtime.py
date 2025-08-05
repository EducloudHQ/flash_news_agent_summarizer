#!/usr/bin/env python3
import argparse, os, sys, time, json
import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime

def _status_to_tuple(status_resp):
    """Return (status, arn) from toolkit status() response."""
    endpoint = None
    if hasattr(status_resp, "endpoint"):
        endpoint = status_resp.endpoint
    elif isinstance(status_resp, dict):
        endpoint = status_resp.get("endpoint")
    if not isinstance(endpoint, dict):
        return None, None
    status = endpoint.get("status")
    arn = endpoint.get("arn") or endpoint.get("endpointArn")
    return status, arn

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", required=True, help="ECR image URI (repo:tag or @digest) already pushed")
    p.add_argument("--agent-name", required=True, help="AgentCore runtime name")
    p.add_argument("--role-arn", required=True, help="Execution role ARN created by CDK")
    p.add_argument("--region", default=os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION"))
    p.add_argument("--workdir", default="strands", help="Dir containing flash_news_agent.py (won’t build)")
    p.add_argument("--ssm-param", default="/agentcore/flash-news/runtime-arn")
    p.add_argument("--entrypoint", default="flash_news_agent.py", help="Only used in rare fallback path")
    args = p.parse_args()

    if args.workdir and args.workdir != ".":
        if not os.path.isdir(args.workdir):
            print(f"❌ workdir '{args.workdir}' not found", file=sys.stderr)
            sys.exit(1)
        os.chdir(args.workdir)

    # Region fallback
    region = args.region or (Session().region_name or "us-east-1")

    rt = Runtime()

    # --- Try to launch/update runtime using the already-pushed image ---
    launched = False
    try_order = [
        # (callable, kwargs)
        (rt.launch, {"image_uri": args.image, "execution_role": args.role_arn, "agent_name": args.agent_name, "region": region}),
        (rt.launch, {"container_uri": args.image, "role_arn": args.role_arn, "name": args.agent_name, "region": region}),
        (rt.launch, {"image_uri": args.image, "role_arn": args.role_arn, "name": args.agent_name, "region": region}),
    ]
    for fn, kw in try_order:
        try:
            print(f"→ Trying Runtime.launch with args: {kw}")
            fn(**kw)  # if signature matches, we’re done
            launched = True
            break
        except TypeError as e:
            print(f"… signature didn’t match ({e}); trying next")
        except Exception as e:
            print(f"… launch attempt failed: {e}", file=sys.stderr)

    # --- Fallback: do a minimal configure (no build) then launch() ---
    if not launched:
        # We *really* don’t want to rebuild; try common knobs
        cfg_try = [
            {"entrypoint": args.entrypoint, "execution_role": args.role_arn, "auto_create_ecr": False,
             "region": region, "agent_name": args.agent_name, "skip_build": True},
            {"entrypoint": args.entrypoint, "execution_role": args.role_arn, "auto_create_ecr": False,
             "region": region, "agent_name": args.agent_name},
        ]
        # Some toolkit builds accept an env var telling it to reuse an existing image
        os.environ["AGENTCORE_IMAGE_URI"] = args.image
        configured = False
        for kw in cfg_try:
            try:
                print(f"→ Fallback configure with args: {kw}")
                rt.configure(**kw)
                configured = True
                break
            except TypeError as e:
                print(f"… configure signature didn’t match ({e}); trying next")
        if not configured:
            print("✖︎ Could not configure runtime without build; aborting.", file=sys.stderr)
            sys.exit(2)
        print("→ Launching after minimal configure")
        rt.launch()

    # --- Wait for terminal status & capture ARN ---
    terminal = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    arn = None
    while True:
        st, arn = _status_to_tuple(rt.status())
        print(f"Agent status: {st or 'UNKNOWN'}")
        if st in terminal:
            break
        time.sleep(10)

    # --- Persist ARN to SSM so your CDK stack can read it ---
    if arn:
        boto3.client("ssm").put_parameter(Name=args.ssm_param, Value=arn, Type="String", Overwrite=True)
        print(f"✔︎ Stored runtime ARN in SSM: {arn}")
    else:
        print("⚠︎ Could not determine runtime ARN; SSM parameter not updated", file=sys.stderr)

if __name__ == "__main__":
    main()
