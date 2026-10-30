import base64
import json

import pytest

from kb_demo.handler import handle
from kb_demo.search import Searcher
from tests.fakes import FakeAws, retrieval_result

KB_ID = "KB00000001"


@pytest.fixture
def aws() -> FakeAws:
    aws = FakeAws()
    aws.runtime.results = [
        retrieval_result("travel.md", 0.78, "Perth: A$255 a night."),
        retrieval_result("expenses.md", 0.33, "Dinner: A$52."),
    ]
    return aws


@pytest.fixture
def searcher(aws) -> Searcher:
    return Searcher(aws.runtime, aws.bedrock, knowledge_base_id=KB_ID, model_id="au.test-model")


def event(body: str | None, *, base64_encoded: bool = False) -> dict:
    """An HTTP API request for POST /ask, in payload format 2.0."""
    if body is not None and base64_encoded:
        body = base64.b64encode(body.encode()).decode()
    request = {"version": "2.0", "routeKey": "POST /ask", "isBase64Encoded": base64_encoded}
    return request if body is None else {**request, "body": body}


def test_a_question_is_answered_with_its_passages(aws, searcher):
    aws.bedrock.reply = "Up to A$255 a night [1]."

    response = handle(event(json.dumps({"question": " Hotel limit in Perth? "})), searcher)

    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"] == "application/json"
    assert json.loads(response["body"]) == {
        "question": "Hotel limit in Perth?",
        "text": "Up to A$255 a night [1].",
        "unavailable": None,
        "passages": [
            {"source": "travel.md", "score": 0.78, "text": "Perth: A$255 a night."},
            {"source": "expenses.md", "score": 0.33, "text": "Dinner: A$52."},
        ],
    }
    assert aws.runtime.requests[0]["knowledgeBaseId"] == KB_ID


def test_a_base64_encoded_body_is_decoded(searcher):
    response = handle(event('{"question": "Hotel limit?"}', base64_encoded=True), searcher)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["question"] == "Hotel limit?"


NO_QUESTION = {
    "no body": None,
    "empty": "",
    "not JSON": "Hotel limit?",
    "not an object": '["Hotel limit?"]',
    "no question": '{"q": "Hotel limit?"}',
    "blank question": '{"question": "   "}',
    "not text": '{"question": 7}',
}


@pytest.mark.parametrize("body", NO_QUESTION.values(), ids=NO_QUESTION.keys())
def test_a_request_without_a_question_gets_400_without_calling_aws(aws, searcher, body):
    response = handle(event(body), searcher)

    assert response["statusCode"] == 400
    assert "question" in json.loads(response["body"])["message"]
    assert aws.runtime.requests == aws.bedrock.requests == []


def test_a_converse_error_still_returns_the_passages_and_says_why(aws, searcher):
    aws.bedrock.errors["converse"] = "ThrottlingException"

    response = handle(event('{"question": "Hotel limit?"}'), searcher)

    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["text"] is None
    assert body["unavailable"].startswith("ThrottlingException")
    assert [passage["source"] for passage in body["passages"]] == ["travel.md", "expenses.md"]
