import aws_cdk as core
import aws_cdk.assertions as assertions

from flash_news_stream_summarizer.flash_news_stream_summarizer_stack import FlashNewsStreamSummarizerStack

# example tests. To run these tests, uncomment this file along with the example
# resource in flash_news_stream_summarizer/flash_news_stream_summarizer_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = FlashNewsStreamSummarizerStack(app, "flash-news-stream-summarizer")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
