"""Load the corpus into the stack cdk deploy made, and report on it; everything is found by name."""

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from botocore.exceptions import ClientError

from kb_demo.config import Config

log = logging.getLogger(__name__)

# The workflow gives up after 15 minutes (its TimeoutSeconds); wait long enough to hear it.
WORKFLOW_WAIT = 960


@dataclass(frozen=True)
class Clients:
    sts: Any
    s3: Any
    iam: Any
    agent: Any  # bedrock-agent
    sfn: Any  # stepfunctions


class Found(NamedTuple):
    id: str
    status: str


class Ingestion(NamedTuple):
    id: str
    status: str
    statistics: Mapping[str, int]


@dataclass(frozen=True)
class Status:
    bucket: str | None = None
    role: str | None = None
    knowledge_base: Found | None = None
    data_source: Found | None = None
    workflow: str | None = None  # the ingest state machine's ARN
    last_ingestion: Ingestion | None = None


@dataclass(frozen=True)
class IngestReport:
    knowledge_base_id: str
    data_source_id: str
    statistics: Mapping[str, int]
    documents: Mapping[str, str]  # corpus file name -> document status
    execution_arn: str
    seconds: float  # how long the workflow ran


class IngestError(Exception):
    pass


Sleep = Callable[[float], None]
Clock = Callable[[], float]


def wait_for[T](
    check: Callable[[], T | None],
    *,
    what: str,
    timeout: float,
    interval: float = 10.0,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> T:
    """Poll check() until it returns something truthy, or raise TimeoutError."""
    deadline = clock() + timeout
    while not (result := check()):
        if clock() >= deadline:
            raise TimeoutError(f"gave up waiting for {what} after {timeout:.0f} s")
        sleep(interval)
    return result


def ingest(
    clients: Clients, config: Config, *, sleep: Sleep = time.sleep, clock: Clock = time.monotonic
) -> IngestReport:
    """Upload the corpus to the stack's bucket, then run the ingest workflow over it. The workflow
    passes only if every corpus file was indexed: a COMPLETE job alone proves nothing (lab 04.1)."""
    found = status(clients, config)
    kb, ds = found.knowledge_base, found.data_source
    if not (found.bucket and kb and ds and found.workflow):
        raise IngestError("the kb-demo stack isn't deployed; run cdk deploy first")
    if (kb.status, ds.status) != ("ACTIVE", "AVAILABLE"):
        raise IngestError(f"the knowledge base is {kb.status} and the data source {ds.status}")
    files = sorted(config.corpus_dir.glob("*.md"))

    log.info("Bucket %s: syncing %d corpus files", found.bucket, len(files))
    _sync_corpus(clients.s3, found.bucket, files)
    log.info("Running the ingest workflow %s", found.workflow)
    corpus = [file.name for file in files]
    execution = _run(clients.sfn, found.workflow, corpus, sleep=sleep, clock=clock)
    if execution["status"] != "SUCCEEDED":
        reason = ": ".join(filter(None, (execution.get("error"), execution.get("cause"))))
        raise IngestError(
            f"the ingest workflow ended {execution['status']}: {reason or 'no reason given'}"
            f" ({execution['executionArn']})"
        )
    output = json.loads(execution["output"])
    # Step Functions capitalises the API's field names; status prints boto3's spelling.
    statistics = {key[0].lower() + key[1:]: value for key, value in output["statistics"].items()}
    seconds = (execution["stopDate"] - execution["startDate"]).total_seconds()
    return IngestReport(
        kb.id, ds.id, statistics, output["documents"], execution["executionArn"], seconds
    )


def status(clients: Clients, config: Config) -> Status:
    account_id = clients.sts.get_caller_identity()["Account"]
    bucket = config.bucket_name(account_id)
    kb = find_knowledge_base(clients.agent, config.knowledge_base_name)
    ds = _find_data_source(clients.agent, kb.id, config.data_source_name) if kb else None
    return Status(
        bucket=bucket if _bucket_exists(clients.s3, bucket) else None,
        role=config.role_name if _role_arn(clients.iam, config.role_name) else None,
        knowledge_base=kb,
        data_source=ds,
        workflow=_workflow(clients.sfn, config.workflow_arn(account_id)),
        last_ingestion=_last_ingestion(clients.agent, kb.id, ds.id) if kb and ds else None,
    )


def _code(error: ClientError) -> str:
    return error.response["Error"]["Code"]


def _bucket_exists(s3: Any, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        if _code(error) in {"404", "NoSuchBucket"}:
            return False
        raise
    return True


def _keys(s3: Any, bucket: str) -> list[str]:
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
    return [item["Key"] for page in pages for item in page.get("Contents", [])]


def _sync_corpus(s3: Any, bucket: str, files: list[Path]) -> None:
    for file in files:
        s3.put_object(
            Bucket=bucket, Key=file.name, Body=file.read_bytes(), ContentType="text/markdown"
        )
    wanted = {file.name for file in files}
    stale = [{"Key": key} for key in _keys(s3, bucket) if key not in wanted]
    if stale:
        s3.delete_objects(Bucket=bucket, Delete={"Objects": stale, "Quiet": True})


def _workflow(sfn: Any, arn: str) -> str | None:
    try:
        sfn.describe_state_machine(stateMachineArn=arn)
    except ClientError as error:
        if _code(error) == "StateMachineDoesNotExist":
            return None
        raise
    return arn


def _role_arn(iam: Any, role_name: str) -> str | None:
    try:
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]
    except ClientError as error:
        if _code(error) == "NoSuchEntity":
            return None
        raise


def find_knowledge_base(agent: Any, name: str) -> Found | None:
    for page in agent.get_paginator("list_knowledge_bases").paginate():
        for summary in page["knowledgeBaseSummaries"]:
            if summary["name"] == name:
                return Found(summary["knowledgeBaseId"], summary["status"])
    return None


def _find_data_source(agent: Any, kb_id: str, name: str) -> Found | None:
    for page in agent.get_paginator("list_data_sources").paginate(knowledgeBaseId=kb_id):
        for summary in page["dataSourceSummaries"]:
            if summary["name"] == name:
                return Found(summary["dataSourceId"], summary["status"])
    return None


def _run(sfn: Any, arn: str, corpus: list[str], *, sleep: Sleep, clock: Clock) -> dict[str, Any]:
    """Start the workflow over these corpus files and wait for it to end, however it ends."""
    workflow_input = json.dumps({"corpus": corpus})
    execution_arn = sfn.start_execution(stateMachineArn=arn, input=workflow_input)["executionArn"]

    def ended() -> dict[str, Any] | None:
        execution = sfn.describe_execution(executionArn=execution_arn)
        return execution if execution["status"] != "RUNNING" else None

    return wait_for(
        ended, what="the ingest workflow", timeout=WORKFLOW_WAIT, sleep=sleep, clock=clock
    )


def _last_ingestion(agent: Any, kb_id: str, ds_id: str) -> Ingestion | None:
    jobs = agent.list_ingestion_jobs(
        knowledgeBaseId=kb_id,
        dataSourceId=ds_id,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        maxResults=1,
    )["ingestionJobSummaries"]
    if not jobs:
        return None
    return Ingestion(jobs[0]["ingestionJobId"], jobs[0]["status"], jobs[0].get("statistics", {}))
