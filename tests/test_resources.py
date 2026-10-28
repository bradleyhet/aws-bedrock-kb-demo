import pytest
from botocore.exceptions import ClientError

from kb_demo.config import Config
from kb_demo.resources import SetupError, Status, setup, status, teardown, wait_for
from tests.fakes import FakeAws, FakeClock

ACCOUNT = "111122223333"
BUCKET = f"kb-demo-{ACCOUNT}-ap-southeast-2"
TAGS = {"Project": "kb-demo"}


@pytest.fixture
def aws() -> FakeAws:
    return FakeAws(account_id=ACCOUNT)


@pytest.fixture
def config(tmp_path) -> Config:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("travel.md", "vpn.md"):
        (corpus / name).write_text(f"# {name}\n", encoding="utf-8")
    return Config(corpus_dir=corpus)


def run_setup(aws: FakeAws, config: Config):
    clock = FakeClock()
    return setup(aws.clients(), config, sleep=clock.sleep, clock=clock.time)


def test_wait_for_gives_up_after_its_timeout():
    clock = FakeClock()

    with pytest.raises(TimeoutError, match="knowledge base"):
        wait_for(
            lambda: None, what="knowledge base", timeout=60, sleep=clock.sleep, clock=clock.time
        )

    assert clock.now >= 60


def test_setup_on_an_empty_account_creates_everything(aws, config):
    report = run_setup(aws, config)

    assert report.documents == {"travel.md": "INDEXED", "vpn.md": "INDEXED"}
    found = status(aws.clients(), config)
    assert found.bucket == BUCKET
    assert found.role == "kb-demo-kb-role"
    assert found.knowledge_base == (report.knowledge_base_id, "ACTIVE")
    assert found.data_source == (report.data_source_id, "AVAILABLE")
    assert found.last_ingestion.status == "COMPLETE"
    assert sorted(aws.s3.buckets[BUCKET]) == ["travel.md", "vpn.md"]
    kb_tags = aws.agent.knowledge_bases[report.knowledge_base_id]["tags"]
    assert aws.s3.tags[BUCKET] == aws.iam.roles["kb-demo-kb-role"]["Tags"] == kb_tags == TAGS


def test_a_second_setup_creates_nothing_new(aws, config):
    first = run_setup(aws, config)

    second = run_setup(aws, config)

    assert second.knowledge_base_id == first.knowledge_base_id
    assert second.data_source_id == first.data_source_id
    assert len(aws.agent.knowledge_bases) == len(aws.agent.data_sources) == 1
    assert second.documents == {"travel.md": "INDEXED", "vpn.md": "INDEXED"}


def test_setup_deletes_objects_no_longer_in_the_corpus(aws, config):
    run_setup(aws, config)
    (config.corpus_dir / "vpn.md").unlink()

    report = run_setup(aws, config)

    assert sorted(aws.s3.buckets[BUCKET]) == ["travel.md"]
    assert report.documents == {"travel.md": "INDEXED"}


def test_setup_fails_when_a_document_fails_to_ingest(aws, config):
    aws.agent.document_status["vpn.md"] = "FAILED"

    with pytest.raises(SetupError, match=r"1 failed; not indexed: vpn\.md \(FAILED\)"):
        run_setup(aws, config)


def test_setup_fails_when_ingestion_completes_having_indexed_nothing(aws, config):
    aws.agent.index_nothing = True

    with pytest.raises(SetupError, match=r"scanned 0 of 2 .*travel\.md \(missing\)"):
        run_setup(aws, config)


def run_teardown(aws: FakeAws, config: Config) -> Status:
    clock = FakeClock()
    return teardown(aws.clients(), config, sleep=clock.sleep, clock=clock.time)


def test_teardown_removes_everything(aws, config):
    run_setup(aws, config)

    remaining = run_teardown(aws, config)

    assert remaining == Status()
    assert aws.s3.buckets == aws.iam.roles == aws.agent.knowledge_bases == {}


HALF_BUILT = {
    "untouched account": None,
    "role policy refused": ("iam", "put_role_policy"),
    "knowledge base refused": ("agent", "create_knowledge_base"),
    "data source refused": ("agent", "create_data_source"),
}


@pytest.mark.parametrize("broken_at", HALF_BUILT.values(), ids=list(HALF_BUILT))
def test_teardown_of_a_half_built_account_succeeds(aws, config, broken_at):
    if broken_at:
        service, method = broken_at
        getattr(aws, service).errors[method] = "AccessDeniedException"
        with pytest.raises(ClientError, match="AccessDenied"):
            run_setup(aws, config)
        getattr(aws, service).errors.clear()

    assert run_teardown(aws, config) == Status()
