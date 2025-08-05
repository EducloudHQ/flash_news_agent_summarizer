import aws_cdk as cdk
from constructs import Construct
from flash_news_stream_summarizer.flash_news_stream_summarizer_stack import FlashNewsStreamSummarizerStack


class PipelineAppStage(cdk.Stage):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        FlashNewsStreamSummarizerStack(self, "FlashNewsStreamSummarizerStack" )
