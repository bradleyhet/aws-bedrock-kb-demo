"""The kb-demo command, over fakes set up the way cdk deploy leaves the account."""

import io
import sys

from kb_demo import cli
from kb_demo.config import Config
from tests.fakes import FakeAws


def test_env_ends_lines_with_lf_even_where_stdout_writes_crlf(monkeypatch):
    # Windows' text-mode stdout turns each \n into \r\n, so env $(kb-demo env) would pass the
    # knowledge base ID on with a trailing \r, and Retrieve rejects it.
    aws = FakeAws()
    aws.deploy(Config())
    [kb_id] = aws.agent.knowledge_bases
    monkeypatch.setattr(cli, "make_clients", lambda config: aws.clients())
    monkeypatch.setenv("KB_DEMO_MODEL_ID", "test-model")
    raw = io.BytesIO()
    windows_stdout = io.TextIOWrapper(raw, encoding="utf-8", newline="\r\n")
    monkeypatch.setattr(sys, "stdout", windows_stdout)

    assert cli.main(["env"]) == 0

    windows_stdout.flush()
    expected = f"KB_DEMO_KNOWLEDGE_BASE_ID={kb_id}\nKB_DEMO_MODEL_ID=test-model\n"
    assert raw.getvalue() == f"{expected}AWS_REGION=ap-southeast-2\n".encode()
