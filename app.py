#!/usr/bin/env python3
import os

import aws_cdk as cdk

from flash_news_stream_summarizer.flash_news_stream_summarizer_stack import FlashNewsStreamSummarizerStack

app = cdk.App()
FlashNewsStreamSummarizerStack(app, "FlashNewsStreamSummarizerStack",

                               env=cdk.Environment(account=os.getenv('CDK_DEFAULT_ACCOUNT'),
                                                   region=os.getenv('CDK_DEFAULT_REGION')),

                               )

app.synth()
