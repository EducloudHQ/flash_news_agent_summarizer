from aws_cdk import (
    Stack,
    aws_codebuild as codebuild,
    aws_iam as iam,
)
import aws_cdk as cdk
from aws_cdk.pipelines import (
    ManualApprovalStep, CodePipelineSource, CodeBuildOptions,
    CodePipeline, ShellStep, CodeBuildStep
)
from constructs import Construct
from pipeline_app_stage import PipelineAppStage


class AgentPipelineStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs):
        super().__init__(scope, id, **kwargs)

        # ❶ Source
        source = CodePipelineSource.git_hub(
            "EducloudHQ/flash_news_agent_summarizer",
            "pipeline",
            # authentication=cdk.SecretValue.secrets_manager("GITHUB_TOKEN"),  # if using PAT
            # or use CodePipelineSource.connection(...) with CodeStar Connections
        )

        # ❷ Global CodeBuild defaults (Docker-in-Docker)
        codebuild_defaults = CodeBuildOptions(
            build_environment=codebuild.BuildEnvironment(privileged=True)
        )

        # ❸ Pipeline skeleton
        pipeline = CodePipeline(
            self,
            "BedrockAgentPipeline",
            synth=ShellStep(
                "Synth",
                input=source,
                commands=[
                    "python -m pip install -r requirements.txt",
                    "npm i -g aws-cdk",
                    "cdk synth",
                ],
            ),
            code_build_defaults=codebuild_defaults,
        )

        # Agent execution role (unchanged)
        agent_name = "flash_news_strands_agent"
        agent_role = iam.Role(
            self,
            "FlashNewsAgentRole",
            role_name=f"agentcore-{agent_name}-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
            inline_policies={
                "AgentCorePolicy": iam.PolicyDocument(statements=[
                    iam.PolicyStatement(
                        actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        actions=["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                        resources=[f"arn:aws:ecr:{self.region}:{self.account}:repository/*"],
                    ),
                    iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"]),
                    iam.PolicyStatement(
                        actions=["logs:CreateLogGroup", "logs:DescribeLogGroups"],
                        resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:*"],
                    ),
                    iam.PolicyStatement(
                        actions=["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
                        resources=[
                            f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*",
                            f"arn:aws:logs:{self.region}:{self.account}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
                        ],
                    ),
                    iam.PolicyStatement(
                        actions=[
                            "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                            "xray:GetSamplingRules", "xray:GetSamplingTargets",
                        ],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        actions=["cloudwatch:PutMetricData"],
                        resources=["*"],
                        conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                    ),
                    iam.PolicyStatement(
                        actions=[
                            "bedrock-agentcore:GetWorkloadAccessToken",
                            "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                            "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
                        ],
                        resources=[
                            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default",
                            f"arn:aws:bedrock-agentcore:{self.region}:{self.account}:workload-identity-directory/default/workload-identity/{agent_name}-*",
                        ],
                    ),
                ])
            },
        )

        # ❹ Your ECR build + Runtime upsert step — now saved to a variable
        deploy_agent_step = CodeBuildStep(
            "BuildPushAndUpsert",
            input=source,
            commands=[
                # 1) ECR login/create
                "export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)",
                "export REPO_URI=$AWS_ACCOUNT_ID.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com/flash-news-strands",
                "aws ecr describe-repositories --repository-names flash-news-strands || "
                "aws ecr create-repository --repository-name flash-news-strands",
                "aws ecr get-login-password | docker login --username AWS --password-stdin $REPO_URI",

                # 2) Buildx push (ARM64)
                "docker buildx create --use --name agentcore_builder || true",
                "docker buildx build --platform linux/arm64 "
                "  -t $REPO_URI:$CODEBUILD_RESOLVED_SOURCE_VERSION "
                "  --push .",

                # 3) Upsert AgentCore runtime + write SSM param
                "python strands/upsert_runtime.py "
                "  --image $REPO_URI:$CODEBUILD_RESOLVED_SOURCE_VERSION "
                "  --agent-name flash_news_strands_agent "
                "  --role-arn $AGENT_ROLE_ARN"
            ],
            env={
                "AWS_DEFAULT_REGION": self.region,
                "AGENT_ROLE_ARN": agent_role.role_arn,
            },
            role_policy_statements=[
                iam.PolicyStatement(
                    actions=[
                        "ecr:GetAuthorizationToken",
                        "ecr:DescribeRepositories",
                        "ecr:CreateRepository",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                        "ecr:PutImage",
                        "bedrock-agentcore-control:*",
                        "iam:PassRole",
                        "ssm:PutParameter",
                    ],
                    resources=["*"],
                )
            ],
            # build_environment omitted here because we set code_build_defaults to privileged=True
        )

        # 🔹 ❺ Put the agent build in its own wave so it always runs
        agent_wave = pipeline.add_wave("AgentImage")
        agent_wave.add_pre(deploy_agent_step)

        # 🔹 ❻ Then deploy your application stage
        pipeline.add_stage(
            PipelineAppStage(
                self, "PipelineStage",
                env=cdk.Environment(account=self.account, region=self.region),
            )
        ).add_post(ManualApprovalStep("approval"))
