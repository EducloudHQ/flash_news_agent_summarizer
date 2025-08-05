from aws_cdk import (
    Stack,
    Duration,
    CfnOutput,
    aws_kinesis as kinesis,
    aws_lambda as _lambda,
    aws_lambda_event_sources as events,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_iam as iam,
)
from constructs import Construct
from aws_cdk.aws_lambda_python_alpha import PythonFunction

class FlashNewsStreamSummarizerStack(Stack):
    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        news_stream = kinesis.Stream(
            self,
            "NewsRawStream",
            stream_name="flash-news-raw",
            shard_count=2,
            retention_period=Duration.hours(24),
        )

        #  SNS topic for downstream fan‑out
        flash_topic = sns.Topic(
            self,
            "FlashNewsTopic",
            display_name="Flash News Digest",
        )

        # Optional e‑mail subscription

        flash_topic.add_subscription(subs.EmailSubscription("treyrosius@gmail.com"))

        # Lambda consumer that calls the AgentCore Runtime
        summarizer_fn = PythonFunction(
            self,
            "InvokeAgentSummarizer",
            runtime=_lambda.Runtime.PYTHON_3_12,
            architecture=_lambda.Architecture.ARM_64,
            handler="main",
            entry="./lambda",
            index="invoke_agent.py",
            memory_size=512,
            timeout=Duration.seconds(30),
            environment={
                "AGENT_ARN": "arn:aws:bedrock-agentcore:us-east-1:132260253285:runtime/flash_news_strands_agent-77mL4dBtst",
                "TOPIC_ARN": flash_topic.topic_arn,
            },
        )

        news_stream.grant_read(summarizer_fn)
        flash_topic.grant_publish(summarizer_fn)

        summarizer_fn.add_event_source(
            events.KinesisEventSource(
                news_stream,
                starting_position=_lambda.StartingPosition.TRIM_HORIZON,
                batch_size=1,
                parallelization_factor=2,
                retry_attempts=3,
            )
        )

        # Allow the function to hit AgentCore Runtime (tighten ARN scope if you like)
        summarizer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:InvokeAgentRuntime"],
                resources=["*"],
            )
        )

        # CloudFormation outputs
        CfnOutput(self, "NewsStreamName", value=news_stream.stream_name)
        CfnOutput(self, "FlashTopicArn", value=flash_topic.topic_arn)
        CfnOutput(self, "SummarizerFunctionName", value=summarizer_fn.function_name)
