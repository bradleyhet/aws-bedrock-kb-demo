"""Lambda handler for POST /ask: the Searcher behind an HTTP API that requires IAM authorisation."""

import base64
import json
import logging
import os
from functools import cache
from typing import Any

import boto3
import botocore

from kb_demo.search import Searcher

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
NO_QUESTION = 'Send a JSON body with a question, like {"question": "How do I reset my password?"}'


def handle(event: dict[str, Any], searcher: Searcher) -> dict[str, Any]:
    """Answer one HTTP API request (payload format 2.0)."""
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode()
    try:
        question = json.loads(body).get("question") if body else None
    except (json.JSONDecodeError, AttributeError):
        question = None
    if not isinstance(question, str) or not question.strip():
        return _response(400, {"message": NO_QUESTION})
    return _response(200, searcher.answer(question.strip()).to_json())


@cache
def _searcher() -> Searcher:
    # Built once per execution environment, so warm invocations reuse the clients.
    region = os.environ["AWS_REGION"]
    log.info("boto3 %s, botocore %s", boto3.__version__, botocore.__version__)
    return Searcher(
        boto3.client("bedrock-agent-runtime", region_name=region),
        boto3.client("bedrock-runtime", region_name=region),
        knowledge_base_id=os.environ["KB_DEMO_KNOWLEDGE_BASE_ID"],
        model_id=os.environ["KB_DEMO_MODEL_ID"],
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, _searcher())


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }
