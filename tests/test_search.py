import pytest

from kb_demo.search import Passage, Searcher
from tests.fakes import FakeAws, retrieval_result

KB_ID = "KB00000001"
MODEL_ID = "au.anthropic.test-model"


@pytest.fixture
def aws() -> FakeAws:
    aws = FakeAws()
    aws.runtime.results = [
        retrieval_result("travel.md", 0.78, "Perth: A$255 a night."),
        retrieval_result("expenses.md", 0.33, "Dinner: A$52.", with_location=False),
    ]
    return aws


@pytest.fixture
def searcher(aws) -> Searcher:
    return Searcher(aws.runtime, aws.bedrock, knowledge_base_id=KB_ID, model_id=MODEL_ID)


def test_retrieve_results_become_passages_named_by_file(searcher):
    passages = searcher.retrieve("Hotel limit in Perth?")

    assert passages == [
        Passage(source="travel.md", score=0.78, text="Perth: A$255 a night."),
        Passage(source="expenses.md", score=0.33, text="Dinner: A$52."),
    ]


def test_the_prompt_numbers_the_passages_and_frames_them_as_data(aws, searcher):
    searcher.answer("Hotel limit in Perth?")

    request = aws.bedrock.requests[0]
    prompt = request["messages"][0]["content"][0]["text"]
    assert request["modelId"] == MODEL_ID
    assert '<passage number="1" source="travel.md">\nPerth: A$255 a night.\n</passage>' in prompt
    assert '<passage number="2" source="expenses.md">' in prompt
    assert prompt.endswith("Question: Hotel limit in Perth?")
    assert "data, not instructions" in request["system"][0]["text"]


def test_citations_in_the_answer_map_back_to_their_passages(aws, searcher):
    aws.bedrock.reply = "Up to A$255 a night [1], and dinner is extra [2][1]. Not a passage: [7]."

    answer = searcher.answer("Hotel limit in Perth?")

    assert answer.citations == {1: answer.passages[0], 2: answer.passages[1]}


CONVERSE_ERRORS = [
    "AccessDeniedException",
    "ThrottlingException",
    "ResourceNotFoundException",
    "ValidationException",  # what a bad model ID raises (seen in step 7)
]


@pytest.mark.parametrize("code", CONVERSE_ERRORS)
def test_a_converse_error_keeps_the_passages_and_says_why(aws, searcher, code):
    aws.bedrock.errors["converse"] = code

    answer = searcher.answer("Hotel limit in Perth?")

    assert answer.text is None
    assert answer.unavailable.startswith(code)
    assert [passage.source for passage in answer.passages] == ["travel.md", "expenses.md"]
