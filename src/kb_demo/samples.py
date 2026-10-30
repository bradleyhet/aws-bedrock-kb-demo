"""Sample questions offered on the page, each answered by exactly one corpus file."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Sample:
    question: str
    source: str  # the corpus file retrieval should rank first
    fact: str  # text the answer depends on, found in that file and no other


# Nothing in the handbook covers this, so the answer must be the decline.
OFF_CORPUS = "How many weeks of parental leave do I get?"

SAMPLES = (
    Sample(
        question="What's the most I can spend on a hotel night in Perth?",
        source="travel.md",
        fact="A$255",
    ),
    Sample(
        question="How soon after a near miss do I have to log it?",
        source="incident-reporting.md",
        fact="Within 4 hours",
    ),
    Sample(
        question="I've cracked my laptop screen for the second time. Will it cost me anything?",
        source="laptop-replacement.md",
        fact="A$250",
    ),
)
