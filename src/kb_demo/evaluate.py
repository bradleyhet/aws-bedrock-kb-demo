"""The release gate: ask the sample questions and an off-corpus one, and check every answer."""

from dataclasses import dataclass

from kb_demo.samples import OFF_CORPUS, SAMPLES
from kb_demo.search import NOT_COVERED, Answerer


@dataclass(frozen=True)
class Check:
    question: str
    passed: bool
    detail: str


def evaluate(searcher: Answerer) -> list[Check]:
    """Each sample must cite its file and state its fact; the off-corpus one must be declined."""
    checks = []
    for sample in SAMPLES:
        answer = searcher.answer(sample.question)
        cited = sorted({passage.source for passage in answer.citations.values()})
        if answer.unavailable:
            problem = f"no answer: {answer.unavailable}"
        elif sample.source not in cited:
            problem = f"cited {cited or 'nothing'}, not {sample.source}"
        elif sample.fact.casefold() not in (answer.text or "").casefold():
            problem = f"the answer doesn't say {sample.fact!r}"
        else:
            problem = None
        detail = problem or f"cited {sample.source} and said {sample.fact!r}"
        checks.append(Check(sample.question, problem is None, detail))

    answer = searcher.answer(OFF_CORPUS)
    declined = (answer.text or "").strip() == NOT_COVERED
    detail = "declined" if declined else f"didn't decline: {answer.text or answer.unavailable}"
    checks.append(Check(OFF_CORPUS, declined, detail))
    return checks
