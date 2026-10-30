import pytest

from kb_demo.config import Config
from kb_demo.resources import IngestError, Status, ingest, status, wait_for
from tests.fakes import FakeAws, FakeClock

ACCOUNT = "111122223333"
BUCKET = f"kb-demo-{ACCOUNT}-ap-southeast-2"


@pytest.fixture
def config(tmp_path) -> Config:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("travel.md", "vpn.md"):
        (corpus / name).write_text(f"# {name}\n", encoding="utf-8")
    return Config(corpus_dir=corpus)


@pytest.fixture
def aws(config) -> FakeAws:
    """An account where cdk deploy has run: the bucket, role, knowledge base and data source."""
    aws = FakeAws(account_id=ACCOUNT)
    aws.deploy(config)
    return aws


def run_ingest(aws: FakeAws, config: Config):
    clock = FakeClock()
    return ingest(aws.clients(), config, sleep=clock.sleep, clock=clock.time)


def test_wait_for_gives_up_after_its_timeout():
    clock = FakeClock()

    with pytest.raises(TimeoutError, match="knowledge base"):
        wait_for(
            lambda: None, what="knowledge base", timeout=60, sleep=clock.sleep, clock=clock.time
        )

    assert clock.now >= 60


def test_ingest_uploads_the_corpus_and_indexes_every_file(aws, config):
    report = run_ingest(aws, config)

    assert report.documents == {"travel.md": "INDEXED", "vpn.md": "INDEXED"}
    assert report.statistics["numberOfDocumentsScanned"] == 2
    assert sorted(aws.s3.buckets[BUCKET]) == ["travel.md", "vpn.md"]
    found = status(aws.clients(), config)
    assert found.knowledge_base == (report.knowledge_base_id, "ACTIVE")
    assert found.data_source == (report.data_source_id, "AVAILABLE")
    assert found.last_ingestion.status == "COMPLETE"


def test_a_second_ingest_of_an_unchanged_corpus_passes(aws, config):
    run_ingest(aws, config)

    second = run_ingest(aws, config)

    assert second.statistics["numberOfNewDocumentsIndexed"] == 0
    assert second.documents == {"travel.md": "INDEXED", "vpn.md": "INDEXED"}


def test_ingest_deletes_objects_no_longer_in_the_corpus(aws, config):
    run_ingest(aws, config)
    (config.corpus_dir / "vpn.md").unlink()

    report = run_ingest(aws, config)

    assert sorted(aws.s3.buckets[BUCKET]) == ["travel.md"]
    assert report.documents == {"travel.md": "INDEXED"}


def test_ingest_fails_when_a_document_fails_to_ingest(aws, config):
    aws.agent.document_status["vpn.md"] = "FAILED"

    with pytest.raises(IngestError, match=r"1 failed; not indexed: vpn\.md \(FAILED\)"):
        run_ingest(aws, config)


def test_ingest_fails_when_ingestion_completes_having_indexed_nothing(aws, config):
    aws.agent.index_nothing = True

    with pytest.raises(IngestError, match=r"scanned 0 of 2 .*travel\.md \(missing\)"):
        run_ingest(aws, config)


def test_before_cdk_deploy_ingest_says_to_deploy_and_status_finds_nothing(config):
    empty = FakeAws(account_id=ACCOUNT)

    with pytest.raises(IngestError, match="cdk deploy"):
        run_ingest(empty, config)

    assert status(empty.clients(), config) == Status()
    assert empty.s3.buckets == {}
