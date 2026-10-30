import re

import pytest

from kb_demo.config import Config
from kb_demo.resources import IngestError, Status, ingest, status, wait_for
from tests.fakes import FakeAws, FakeClock

ACCOUNT = "111122223333"
BUCKET = f"kb-demo-{ACCOUNT}-ap-southeast-2"
EXECUTIONS = f"arn:aws:states:ap-southeast-2:{ACCOUNT}:execution:kb-demo-ingest:"


@pytest.fixture
def config(tmp_path) -> Config:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("travel.md", "vpn.md"):
        (corpus / name).write_text(f"# {name}\n", encoding="utf-8")
    return Config(corpus_dir=corpus)


@pytest.fixture
def aws(config) -> FakeAws:
    """An account where cdk deploy has run: the bucket, role, knowledge base, data source and
    ingest workflow."""
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


def test_ingest_uploads_the_corpus_then_runs_the_workflow_over_it(aws, config):
    report = run_ingest(aws, config)

    assert sorted(aws.s3.buckets[BUCKET]) == ["travel.md", "vpn.md"]
    assert aws.sfn.inputs == [{"corpus": ["travel.md", "vpn.md"]}]
    assert report.documents == {"travel.md": "INDEXED", "vpn.md": "INDEXED"}
    # In boto3's spelling, as status prints it, though Step Functions capitalises field names.
    assert report.statistics["numberOfDocumentsScanned"] == 2
    assert report.execution_arn.startswith(EXECUTIONS)
    assert report.seconds == 75
    found = status(aws.clients(), config)
    assert found.workflow == config.workflow_arn(ACCOUNT)
    assert found.last_ingestion.status == "COMPLETE"


def test_ingest_deletes_objects_no_longer_in_the_corpus(aws, config):
    run_ingest(aws, config)
    (config.corpus_dir / "vpn.md").unlink()

    report = run_ingest(aws, config)

    assert sorted(aws.s3.buckets[BUCKET]) == ["travel.md"]
    assert aws.sfn.inputs[-1] == {"corpus": ["travel.md"]}
    assert report.documents == {"travel.md": "INDEXED"}


def test_ingest_fails_with_the_workflows_reason_when_the_workflow_fails(aws, config):
    cause = "ingestion job JOB00000003 did not index the corpus: not indexed: vpn.md (FAILED)"
    aws.sfn.ending = ("FAILED", "CorpusNotIndexed", cause)

    expected = rf"FAILED: CorpusNotIndexed: {re.escape(cause)}"
    with pytest.raises(IngestError, match=expected) as raised:
        run_ingest(aws, config)

    assert EXECUTIONS in str(raised.value)


def test_ingest_fails_when_the_workflow_times_out(aws, config):
    aws.sfn.ending = ("TIMED_OUT", None, None)

    with pytest.raises(IngestError, match="TIMED_OUT: no reason given"):
        run_ingest(aws, config)


def test_before_cdk_deploy_ingest_says_to_deploy_and_status_finds_nothing(config):
    empty = FakeAws(account_id=ACCOUNT)

    with pytest.raises(IngestError, match="cdk deploy"):
        run_ingest(empty, config)

    assert status(empty.clients(), config) == Status()
    assert empty.s3.buckets == {}


def test_a_stack_deployed_before_the_workflow_existed_says_to_deploy(aws, config):
    aws.sfn.state_machines.clear()

    with pytest.raises(IngestError, match="cdk deploy"):
        run_ingest(aws, config)

    assert aws.sfn.inputs == []
