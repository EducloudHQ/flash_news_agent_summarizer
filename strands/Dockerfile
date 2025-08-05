# Python 3.12 ARM64 base (3.13 isn’t GA yet)
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && pip install "aws-opentelemetry-distro>=0.10.0"

# non-root user (same as before)
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

ENV AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 DOCKER_CONTAINER=1
EXPOSE 8080 8000

COPY . .
CMD ["opentelemetry-instrument", "python", "-m", "flash_news_agent"]
