import pytest
from botocore.credentials import Credentials

from kb_demo.remote import ApiError, ApiSearcher
from kb_demo.search import Searcher
from tests.fakes import FakeApi, FakeAws, retrieval_result

API_URL = "https://abc123.execute-api.ap-southeast-2.amazonaws.com/"
CREDENTIALS = Credentials("AKIDEXAMPLE", "not-a-secret", "session-token")


@pytest.fixture
def searcher() -> Searcher:
    aws = FakeAws()
    aws.runtime.results = [
        retrieval_result("travel.md", 0.7772, "Perth: A$255 a night."),
        retrieval_result("expenses.md", 0.3297, "Dinner: A$52."),
    ]
    aws.bedrock.reply = "Up to A$255 a night [1]."
    return Searcher(aws.runtime, aws.bedrock, knowledge_base_id="KB00000001", model_id="au.test")


@pytest.fixture
def api(searcher) -> FakeApi:
    return FakeApi(searcher)


def remote(api: FakeApi, url: str = API_URL) -> ApiSearcher:
    return ApiSearcher(url, CREDENTIALS, region="ap-southeast-2", send=api.send)


@pytest.mark.parametrize("url", [API_URL, API_URL.rstrip("/")], ids=["slash", "no slash"])
def test_the_question_is_posted_to_ask_and_signed_for_execute_api(api, url):
    remote(api, url).answer("Hotel limit in Perth?")

    request = api.requests[0]
    assert (request.method, request.url) == ("POST", API_URL + "ask")
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/"
    )
    assert "/ap-southeast-2/execute-api/aws4_request" in request.headers["Authorization"]
    assert request.headers["X-Amz-Security-Token"] == "session-token"


def test_the_answer_arrives_as_the_handler_built_it(api, searcher):
    assert remote(api).answer("Hotel limit in Perth?") == searcher.answer("Hotel limit in Perth?")


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (403, b'{"message":"Forbidden"}', "403: Forbidden"),
        (429, b'{"message":"Too Many Requests"}', "429: Too Many Requests"),
        (502, b"Bad Gateway", "502: Bad Gateway"),
    ],
)
def test_a_refused_request_raises_api_error_with_the_status(api, status, body, reason):
    api.refuse = (status, body)

    with pytest.raises(ApiError, match=reason):
        remote(api).answer("Hotel limit in Perth?")
