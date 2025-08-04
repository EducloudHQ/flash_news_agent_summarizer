import json

import boto3, os, requests, time

# NEWS_API_KEY  = os.environ["NEWS_API_KEY"]
NEWS_API_KEY = "22468d88cf6a4dcfb614c13f2d728a89"
# STREAM_NAME   = os.environ["KINESIS_STREAM"]
STREAM_NAME = "flash-news-raw"
SEEN_IDS = set()  # in prod use DynamoDB for durability
kinesis = boto3.client("kinesis")

URL = ("https://newsapi.org/v2/top-headlines?"
       "language=en&pageSize=100&apiKey=" + NEWS_API_KEY)

while True:
    r = requests.get(URL, timeout=10)
    r.raise_for_status()
    for art in r.json().get("articles", []):
        if art["url"] in SEEN_IDS:
            continue
        SEEN_IDS.add(art["url"])
        payload = {
            "headline": art["title"][:500],  # stay under 1 MB record limit
            "source": art["source"]["name"]
        }
        kinesis.put_record(StreamName=STREAM_NAME,
                           PartitionKey="news",
                           Data=json.dumps(payload))
    time.sleep(190)  # throttle to free-tier limits
