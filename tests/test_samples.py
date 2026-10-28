from pathlib import Path

import pytest

from kb_demo.samples import SAMPLES

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def test_samples_point_at_distinct_corpus_files():
    sources = [sample.source for sample in SAMPLES]
    assert len(set(sources)) == len(sources) == 3
    assert all((CORPUS / source).is_file() for source in sources)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample.source)
def test_sample_fact_appears_only_in_its_source_file(sample):
    holders = [
        path.name for path in sorted(CORPUS.glob("*.md")) if sample.fact in path.read_text("utf-8")
    ]
    assert holders == [sample.source]
