#!/usr/bin/env python3
import argparse, os, sys, time
import boto3
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime

def get_status(rt):
    resp = rt.status()
    ep = getattr(resp, "endpoint", None) if not isinstance(resp, dict) else resp.get("endpoint")
    if not isinstance(ep, dict):
        return None, None
    return ep.get("status"), (ep.get("arn") or ep.get("endpointArn"))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image",       required=True, help="ECR image URI (repo:tag or @digest) already pushed")
    p.add_argument("--agent-name",  required=True, help="AgentCore runtime name")
    p.add_argument("--role-arn",    required=True, help="Execution role ARN created by CDK")
    p.add_argument("--region",      default=os.getenv("AWS_DEFAULT_REGION") or os.getenv("AWS_REGION"))
    p.add_argument("--workdir",     default="strands", help="Dir containing agent files (won’t build)")
    p.add_argument("--ssm-param",   default="/agentcore/flash-news/runtime-arn")
    args = p.parse_args()

    # Workdir (so relative paths in any toolkit internals resolve, though we won’t build)
    if args.workdir and args.workdir != ".":
        if not os.path.isdir(args.workdir):
            print(f"❌ workdir '{args.workdir}' not found", file=sys.stderr); sys.exit(1)
        os.chdir(args.workdir)

    region = args.region or (Session().region_name or "us-east-1")

    # Hint newer toolkit builds that a prebuilt image should be used.
    os.environ["AGENTCORE_IMAGE_URI"] = args.image

    rt = Runtime()

    # Try the known launch signatures that accept a prebuilt image.
    tried = []
    variants = [
        dict(image_uri=args.image, execution_role=args.role_arn, agent_name=args.agent_name, region=region),
        dict(container_uri=args.image, role_arn=args.role_arn, name=args.agent_name, region=region),
        dict(image_uri=args.image, role_arn=args.role_arn, name=args.agent_name, region=region),
    ]
    launched = False
    for kw in variants:
        tried.append(kw)
        try:
            print(f"→ runtime.launch(**{kw})")
            rt.launch(**kw)
            launched = True
            break
        except TypeError as e:
            print(f"… signature mismatch: {e}")
        except Exception as e:
            print(f"… launch failed: {e}", file=sys.stderr)

    if not launched:
        print("✖︎ Could not launch with a prebuilt image using the available signatures.\n"
              "• Ensure you installed a recent toolkit version (e.g. bedrock-agentcore-starter-toolkit>=0.0.15)\n"
              "• Or share the exact Runtime.launch signature supported by your version.",
              file=sys.stderr)
        sys.exit(2)

    # Wait for terminal state and capture ARN
    terminal = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    arn = None
    while True:
        status, arn = get_status(rt)
        print(f"Agent status: {status or 'UNKNOWN'}")
        if status in terminal:
            break
        time.sleep(10)

    if arn:
        boto3.client("ssm").put_parameter(Name=args.ssm_param, Value=arn, Type="String", Overwrite=True)
        print(f"✔︎ Stored runtime ARN in SSM: {arn}")
    else:
        print("⚠︎ Could not determine runtime ARN; SSM not updated", file=sys.stderr)

if __name__ == "__main__":
    main()
