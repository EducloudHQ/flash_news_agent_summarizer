from aws_cdk import (
    Stack,
    aws_codebuild as codebuild,
    aws_iam as iam,
    aws_ecr as ecr,
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

        # ❶ Source (GitHub)
        source = CodePipelineSource.git_hub(
            "EducloudHQ/flash_news_agent_summarizer",
            "pipeline",
            # authentication=cdk.SecretValue.secrets_manager("GITHUB_TOKEN"),
            # or use CodePipelineSource.connection(...)
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

        # Optional: manage ECR repo in CDK (toolkit can also auto-create if needed)
        repo = ecr.Repository(
            self, "FlashNewsRepo",
            repository_name="flash-news-strands",
            image_scan_on_push=True,
        )

        # Execution role for AgentCore runtime (created by CDK)
        agent_name = "flash_news_strands_agent"
        agent_role = iam.Role(
            self,
            "FlashNewsSummarizerAgentRole",
            role_name=f"flash-news-agentcore-{agent_name}-role",
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

        # ❹ Build + upsert via toolkit (local build)
        deploy_agent_step = CodeBuildStep(
            "BuildAndUpsertWithToolkit",
            input=source,
            env={
                "AWS_DEFAULT_REGION": self.region,
                "AGENT_ROLE_ARN": agent_role.role_arn,
                "AGENT_NAME": "flash_news_strands_agent",
                "WORKDIR": "strands",
                "ENTRYPOINT": "flash_news_agent.py",
                "REQUIREMENTS_FILE": "requirements.txt",
            },
            commands=[
                "set -eu",
                'echo "PWD=$(pwd)"; ls -la',
                'echo "Listing $WORKDIR:"; ls -la "$WORKDIR" || true',

                # Install toolkit in the CodeBuild environment
                'python -m pip install --upgrade pip',
                'python -m pip install "bedrock-agentcore-starter-toolkit>=0.1.3" boto3',


                'python strands/upsert_runtime.py '
                '  --agent-name "$AGENT_NAME" '
                '  --role-arn "$AGENT_ROLE_ARN" '
                '  --workdir "$WORKDIR" '
                '  --entrypoint "$ENTRYPOINT" '
                '  --requirements "$REQUIREMENTS_FILE" '
                '  --region "$AWS_DEFAULT_REGION"'
            ],
            role_policy_statements=[
                # Needed for toolkit build & push to ECR
                iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"]),
                iam.PolicyStatement(
                    actions=[
                        "iam:GetRole",
                        "iam:GetRolePolicy",
                        "iam:ListRolePolicies",
                        "iam:ListAttachedRolePolicies",
                    ],
                    resources=[agent_role.role_arn],  # scope to the specific role created above
                ),

                # PULL + PUSH repo-scoped (toolkit/dockerd push)
                iam.PolicyStatement(
                    actions=[
                        # pull-ish
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        # push
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                        "ecr:PutImage",
                    ],
                    resources=[repo.repository_arn],
                ),
                # Allow reading/creating the toolkit's SDK CodeBuild role
                iam.PolicyStatement(
                    actions=[
                        "iam:GetRole",
                        "iam:CreateRole",
                        "iam:AttachRolePolicy",
                        "iam:PutRolePolicy",
                        "iam:TagRole",
                        # You already have iam:PassRole (*). If you want to scope it tightly, add it here:
                        # "iam:PassRole",
                    ],
                    resources=[f"arn:aws:iam::{self.account}:role/AmazonBedrockAgentCoreSDKCodeBuild-*"],
                ),
                iam.PolicyStatement(
                    actions=[
                        "codebuild:CreateProject",
                        "codebuild:UpdateProject",
                        "codebuild:DeleteProject",
                        "codebuild:StartBuild",
                        "codebuild:BatchGetBuilds",
                        "codebuild:BatchGetProjects",
                        "codebuild:ListCuratedEnvironmentImages",
                        "codebuild:BatchGetReportGroups",
                        "codebuild:BatchGetReports",
                    ],
                    resources=["*"],
                ),

                # If toolkit ensures the repo, allow describe/create (these are "*" scoped in ECR)
                iam.PolicyStatement(
                    actions=["ecr:DescribeRepositories", "ecr:CreateRepository"],
                    resources=["*"],
                ),

                # Toolkit → AgentCore control-plane + write SSM + pass the execution role
                iam.PolicyStatement(
                    actions=["bedrock-agentcore-control:*", "iam:PassRole", "ssm:PutParameter"],
                    resources=["*"],
                ),
            ],
        )

        # ❺ Put the build/upsert in its own wave (visible action)
        agent_wave = pipeline.add_wave("AgentImage")
        agent_wave.add_pre(deploy_agent_step)

        # ❻ Then deploy your application stage
        pipeline.add_stage(
            PipelineAppStage(
                self, "PipelineStage",
                env=cdk.Environment(account=self.account, region=self.region),
            )
        ).add_post(ManualApprovalStep("approval"))
