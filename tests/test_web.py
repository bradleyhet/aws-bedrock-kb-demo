import pytest
from botocore.credentials import Credentials
from fastapi.testclient import TestClient
from markupsafe import escape

from kb_demo.remote import ApiSearcher
from kb_demo.samples import SAMPLES
from kb_demo.search import Searcher
from kb_demo.web import create_app
from tests.fakes import FakeApi, FakeAws, retrieval_result

API_URL = "https://abc123.execute-api.ap-southeast-2.amazonaws.com/"


@pytest.fixture
def aws() -> FakeAws:
    aws = FakeAws()
    aws.runtime.results = [
        retrieval_result("travel.md", 0.7772, "Perth: A$255 a night."),
        retrieval_result("expenses.md", 0.3297, "Dinner: A$52."),
    ]
    return aws


@pytest.fixture
def client(aws) -> TestClient:
    searcher = Searcher(
        aws.runtime, aws.bedrock, knowledge_base_id="KB00000001", model_id="au.test-model"
    )
    return TestClient(create_app(searcher))


def test_the_home_page_shows_the_form_and_the_sample_questions(client):
    page = client.get("/")

    assert page.status_code == 200
    assert 'action="/ask"' in page.text
    for sample in SAMPLES:
        assert str(escape(sample.question)) in page.text


def test_asking_shows_the_answer_its_citations_and_the_passages(aws, client):
    aws.bedrock.reply = "Up to A$255 a night [1]."

    page = client.post("/ask", data={"question": "Hotel limit in Perth?"})

    assert page.status_code == 200
    assert 'Up to A$255 a night <a class="cite" href="#passage-1">[1]</a>.' in page.text
    assert 'id="passage-1"' in page.text
    assert 'id="passage-2"' in page.text
    assert "travel.md" in page.text
    assert "0.78" in page.text
    assert "Dinner: A$52." in page.text


def test_a_generation_failure_shows_the_notice_and_the_passages(aws, client):
    aws.bedrock.errors["converse"] = "AccessDeniedException"

    page = client.post("/ask", data={"question": "Hotel limit in Perth?"})

    assert page.status_code == 200
    assert "Answer generation unavailable: AccessDeniedException" in page.text
    assert "Perth: A$255 a night." in page.text


@pytest.mark.parametrize("question", ["", "   "], ids=["empty", "blank"])
def test_an_empty_question_returns_400_without_calling_aws(aws, client, question):
    page = client.post("/ask", data={"question": question})

    assert page.status_code == 400
    assert "Type a question first." in page.text
    assert aws.runtime.requests == aws.bedrock.requests == []


def test_an_api_refusal_shows_why_and_returns_502():
    api = FakeApi(searcher=None)
    api.refuse = (403, b'{"message":"Forbidden"}')
    credentials = Credentials("AKIDEXAMPLE", "not-a-secret")
    remote = ApiSearcher(API_URL, credentials, region="ap-southeast-2", send=api.send)
    client = TestClient(create_app(remote))

    page = client.post("/ask", data={"question": "Hotel limit in Perth?"})

    assert page.status_code == 502
    assert "The API answered 403: Forbidden" in page.text


def test_without_a_knowledge_base_the_page_says_to_run_setup_first():
    client = TestClient(create_app(None))

    home = client.get("/")
    ask = client.post("/ask", data={"question": "Hotel limit in Perth?"})

    assert "Run <code>kb-demo setup</code> first" in home.text
    assert ask.status_code == 503
    assert "Run <code>kb-demo setup</code> first" in ask.text
