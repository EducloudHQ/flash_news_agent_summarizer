from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel
import re, textwrap

bedrock_model = BedrockModel(
    model_id="anthropic.claude-3-5-sonnet-20240620-v1:0",
    region_name='us-east-1',
    temperature=0.3,
    cache_tools='default',

)
app = BedrockAgentCoreApp()  # ← 3 LOC to run in Runtime :contentReference[oaicite:1]{index=1}
agent = Agent(
    model=bedrock_model,
    system_prompt=textwrap.dedent("""
      You are FlashNews, a real-time headline summarizer.
      • Produce a ≤200-character digest in the same language.
      • Remove URLs and source names.
      • Output JSON: {"headline": "...", "digest": "..."}.
    """)
)


@app.entrypoint
def invoke(payload):
    headline = payload["headline"]
    # Optional guard-clause: ignore sports scores
    if re.match(r".*\b(\d+–\d+)\b.*", headline):
        return {"headline": headline, "digest": "Skip sports ticker."}

    message = agent(headline).message
    print(message)
    return message


if __name__ == "__main__":
    app.run()
