from dataclasses import replace

import pytest

from kb_demo.evaluate import evaluate
from kb_demo.samples import OFF_CORPUS, SAMPLES
from kb_demo.search import NOT_COVERED, Answer, Passage


class Scripted:
    """Answers each question with the answer the test wrote for it."""

    def __init__(self, answers: dict[str, Answer]) -> None:
        self.answers = answers

    def answer(self, question: str) -> Answer:
        return self.answers[question]


def good_answers() -> dict[str, Answer]:
    answers = {
        sample.question: Answer(
            sample.question,
            [Passage(sample.source, 0.8, f"... {sample.fact} ..."), Passage("vpn.md", 0.3, "...")],
            f"{sample.fact.lower()}, as the policy says [1].",
        )
        for sample in SAMPLES
    }
    answers[OFF_CORPUS] = Answer(OFF_CORPUS, [Passage("vpn.md", 0.2, "...")], NOT_COVERED)
    return answers


def failures(answers: dict[str, Answer]) -> dict[str, str]:
    checks = evaluate(Scripted(answers))
    return {check.question: check.detail for check in checks if not check.passed}


def test_good_answers_pass_every_check():
    checks = evaluate(Scripted(good_answers()))

    assert [check.question for check in checks] == [s.question for s in SAMPLES] + [OFF_CORPUS]
    assert all(check.passed for check in checks)


def test_an_answer_citing_the_wrong_file_fails():
    answers = good_answers()
    first = SAMPLES[0].question
    answers[first] = replace(answers[first], text=f"{SAMPLES[0].fact} [2].")

    assert failures(answers) == {first: f"cited ['vpn.md'], not {SAMPLES[0].source}"}


def test_an_answer_without_citations_fails():
    answers = good_answers()
    first = SAMPLES[0].question
    answers[first] = replace(answers[first], text=f"It's {SAMPLES[0].fact}.")

    assert failures(answers) == {first: f"cited nothing, not {SAMPLES[0].source}"}


def test_an_answer_missing_the_fact_fails():
    answers = good_answers()
    first = SAMPLES[0].question
    answers[first] = replace(answers[first], text="It depends [1].")

    assert failures(answers) == {first: f"the answer doesn't say {SAMPLES[0].fact!r}"}


def test_no_generated_answer_fails_with_the_reason():
    answers = good_answers()
    first = SAMPLES[0].question
    answers[first] = replace(answers[first], text=None, unavailable="ThrottlingException: slow")

    assert failures(answers) == {first: "no answer: ThrottlingException: slow"}


@pytest.mark.parametrize("text", ["About 12 weeks [1].", None], ids=["answered", "no answer"])
def test_the_off_corpus_question_fails_unless_declined(text):
    answers = good_answers()
    answers[OFF_CORPUS] = replace(answers[OFF_CORPUS], text=text)

    assert list(failures(answers)) == [OFF_CORPUS]
