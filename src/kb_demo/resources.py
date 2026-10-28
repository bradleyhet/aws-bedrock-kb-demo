"""Create, inspect and delete the demo's AWS resources, each found by name so reruns are safe."""

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from botocore.exceptions import ClientError

from kb_demo.config import TAGS, Config

log = logging.getLogger(__name__)

POLICY_NAME = "kb-demo-s3-read"
FAILED = {"FAILED", "STOPPED", "DELETE_UNSUCCESSFUL", "UPDATE_UNSUCCESSFUL"}


@dataclass(frozen=True)
class Clients:
    sts: Any
    s3: Any
    iam: Any
    agent: Any  # bedrock-agent


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
    last_ingestion: Ingestion | None = None


@dataclass(frozen=True)
class SetupReport:
    knowledge_base_id: str
    data_source_id: str
    statistics: Mapping[str, int]
    documents: Mapping[str, str]  # corpus file name -> document status


class SetupError(Exception):
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


def setup(
    clients: Clients, config: Config, *, sleep: Sleep = time.sleep, clock: Clock = time.monotonic
) -> SetupReport:
    account_id = clients.sts.get_caller_identity()["Account"]
    bucket = config.bucket_name(account_id)
    files = sorted(config.corpus_dir.glob("*.md"))
    timing = {"sleep": sleep, "clock": clock}

    log.info("Bucket %s: syncing %d corpus files", bucket, len(files))
    _ensure_bucket(clients.s3, bucket, config.region)
    _sync_corpus(clients.s3, bucket, files)
    log.info("Role %s", config.role_name)
    role_arn = _ensure_role(clients.iam, config, account_id, bucket)
    log.info("Knowledge base %s: waiting for ACTIVE", config.knowledge_base_name)
    kb_id = _ensure_knowledge_base(clients.agent, config, role_arn, **timing)
    log.info("Data source %s: waiting for AVAILABLE", config.data_source_name)
    ds_id = _ensure_data_source(clients.agent, config, kb_id, account_id, bucket, **timing)
    log.info("Ingesting the corpus into knowledge base %s", kb_id)
    statistics = _ingest(clients.agent, kb_id, ds_id, **timing)
    documents = _documents(clients.agent, kb_id, ds_id)
    problems = _ingestion_problems(statistics, documents, [file.name for file in files])
    if problems:
        raise SetupError(f"ingestion did not index the corpus: {'; '.join(problems)}")
    return SetupReport(kb_id, ds_id, statistics, documents)


def status(clients: Clients, config: Config) -> Status:
    account_id = clients.sts.get_caller_identity()["Account"]
    bucket = config.bucket_name(account_id)
    kb = _find_knowledge_base(clients.agent, config.knowledge_base_name)
    ds = _find_data_source(clients.agent, kb.id, config.data_source_name) if kb else None
    return Status(
        bucket=bucket if _bucket_exists(clients.s3, bucket) else None,
        role=config.role_name if _role_arn(clients.iam, config.role_name) else None,
        knowledge_base=kb,
        data_source=ds,
        last_ingestion=_last_ingestion(clients.agent, kb.id, ds.id) if kb and ds else None,
    )


def teardown(
    clients: Clients, config: Config, *, sleep: Sleep = time.sleep, clock: Clock = time.monotonic
) -> Status:
    """Delete everything setup made, then report what is left: nothing, if all went well."""
    found = status(clients, config)
    agent, timing = clients.agent, {"timeout": 600, "sleep": sleep, "clock": clock}

    if found.knowledge_base:
        kb_id = found.knowledge_base.id
        if found.data_source:
            ds_id = found.data_source.id
            log.info("Deleting data source %s", ds_id)
            agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)
            wait_for(
                lambda: _gone(
                    lambda: agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)
                ),
                what="the data source to be deleted",
                **timing,
            )
        log.info("Deleting knowledge base %s", kb_id)
        agent.delete_knowledge_base(knowledgeBaseId=kb_id)
        wait_for(
            lambda: _gone(lambda: agent.get_knowledge_base(knowledgeBaseId=kb_id)),
            what="the knowledge base to be deleted",
            **timing,
        )

    if found.bucket:
        log.info("Deleting bucket %s", found.bucket)
        if objects := [{"Key": key} for key in _keys(clients.s3, found.bucket)]:
            clients.s3.delete_objects(
                Bucket=found.bucket, Delete={"Objects": objects, "Quiet": True}
            )
        clients.s3.delete_bucket(Bucket=found.bucket)

    if found.role:
        log.info("Deleting role %s", found.role)
        try:
            clients.iam.delete_role_policy(RoleName=found.role, PolicyName=POLICY_NAME)
        except ClientError as error:
            if _code(error) != "NoSuchEntity":
                raise
        clients.iam.delete_role(RoleName=found.role)
    return status(clients, config)


def _code(error: ClientError) -> str:
    return error.response["Error"]["Code"]


def _tag_list() -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in TAGS.items()]


def _settled(status: str, *, ready: str, what: str, reasons: list[str]) -> bool:
    if status in FAILED:
        raise SetupError(f"{what} is {status}: {'; '.join(reasons) or 'no reason given'}")
    return status == ready


def _gone(get: Callable[[], Any]) -> bool:
    try:
        get()
    except ClientError as error:
        if _code(error) == "ResourceNotFoundException":
            return True
        raise
    return False


def _bucket_exists(s3: Any, bucket: str) -> bool:
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        if _code(error) in {"404", "NoSuchBucket"}:
            return False
        raise
    return True


def _ensure_bucket(s3: Any, bucket: str, region: str) -> None:
    if _bucket_exists(s3, bucket):
        return
    # New buckets get Block Public Access and SSE-S3 by default.
    s3.create_bucket(Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": region})
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": _tag_list()})


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


def _role_arn(iam: Any, role_name: str) -> str | None:
    try:
        return iam.get_role(RoleName=role_name)["Role"]["Arn"]
    except ClientError as error:
        if _code(error) == "NoSuchEntity":
            return None
        raise


def _ensure_role(iam: Any, config: Config, account_id: str, bucket: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            }
        ],
    }
    read_corpus = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}"},
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/*"},
        ],
    }
    arn = _role_arn(iam, config.role_name)
    if arn is None:
        arn = iam.create_role(
            RoleName=config.role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Lets the kb-demo knowledge base read its corpus bucket",
            Tags=_tag_list(),
        )["Role"]["Arn"]
    # Put the policy on every run, so a rerun also repairs it.
    iam.put_role_policy(
        RoleName=config.role_name, PolicyName=POLICY_NAME, PolicyDocument=json.dumps(read_corpus)
    )
    return arn


def _find_knowledge_base(agent: Any, name: str) -> Found | None:
    for page in agent.get_paginator("list_knowledge_bases").paginate():
        for summary in page["knowledgeBaseSummaries"]:
            if summary["name"] == name:
                return Found(summary["knowledgeBaseId"], summary["status"])
    return None


def _ensure_knowledge_base(
    agent: Any, config: Config, role_arn: str, *, sleep: Sleep, clock: Clock
) -> str:
    found = _find_knowledge_base(agent, config.knowledge_base_name)
    if found:
        kb_id = found.id
    else:
        kb_id = agent.create_knowledge_base(
            name=config.knowledge_base_name,
            description="kb-demo: a fictional field-services handbook",
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {"embeddingModelType": "MANAGED"},
            },
            tags=TAGS,
        )["knowledgeBase"]["knowledgeBaseId"]

    def active() -> bool:
        kb = agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]
        return _settled(
            kb["status"],
            ready="ACTIVE",
            what="knowledge base",
            reasons=kb.get("failureReasons", []),
        )

    wait_for(active, what="the knowledge base to be ACTIVE", timeout=600, sleep=sleep, clock=clock)
    return kb_id


def _find_data_source(agent: Any, kb_id: str, name: str) -> Found | None:
    for page in agent.get_paginator("list_data_sources").paginate(knowledgeBaseId=kb_id):
        for summary in page["dataSourceSummaries"]:
            if summary["name"] == name:
                return Found(summary["dataSourceId"], summary["status"])
    return None


def _ensure_data_source(
    agent: Any,
    config: Config,
    kb_id: str,
    account_id: str,
    bucket: str,
    *,
    sleep: Sleep,
    clock: Clock,
) -> str:
    found = _find_data_source(agent, kb_id, config.data_source_name)
    if found:
        ds_id = found.id
    else:
        ds_id = agent.create_data_source(
            knowledgeBaseId=kb_id,
            name=config.data_source_name,
            description="kb-demo corpus bucket",
            dataDeletionPolicy="DELETE",
            dataSourceConfiguration={
                "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                "managedKnowledgeBaseConnectorConfiguration": {
                    "connectorParameters": {
                        "type": "S3",
                        "version": "1",
                        "connectionConfiguration": {
                            "bucketName": bucket,
                            "bucketOwnerAccountId": account_id,
                        },
                        "deletionProtectionConfiguration": {"enableDeletionProtection": False},
                    }
                },
            },
        )["dataSource"]["dataSourceId"]

    def available() -> bool:
        ds = agent.get_data_source(knowledgeBaseId=kb_id, dataSourceId=ds_id)["dataSource"]
        return _settled(
            ds["status"],
            ready="AVAILABLE",
            what="data source",
            reasons=ds.get("failureReasons", []),
        )

    wait_for(
        available, what="the data source to be AVAILABLE", timeout=600, sleep=sleep, clock=clock
    )
    return ds_id


def _ingest(agent: Any, kb_id: str, ds_id: str, *, sleep: Sleep, clock: Clock) -> Mapping[str, int]:
    ids = {"knowledgeBaseId": kb_id, "dataSourceId": ds_id}
    job_id = agent.start_ingestion_job(**ids)["ingestionJob"]["ingestionJobId"]

    def finished() -> dict[str, Any] | None:
        job = agent.get_ingestion_job(**ids, ingestionJobId=job_id)["ingestionJob"]
        reasons = job.get("failureReasons", [])
        done = _settled(job["status"], ready="COMPLETE", what="ingestion job", reasons=reasons)
        return job if done else None

    job = wait_for(finished, what="ingestion to COMPLETE", timeout=900, sleep=sleep, clock=clock)
    return job.get("statistics", {})


def _documents(agent: Any, kb_id: str, ds_id: str) -> dict[str, str]:
    pages = agent.get_paginator("list_knowledge_base_documents").paginate(
        knowledgeBaseId=kb_id, dataSourceId=ds_id
    )
    return {
        detail["identifier"]["s3"]["uri"].rsplit("/", 1)[-1]: detail["status"]
        for page in pages
        for detail in page["documentDetails"]
    }


def _ingestion_problems(
    statistics: Mapping[str, int], documents: Mapping[str, str], expected: list[str]
) -> list[str]:
    """Why ingestion can't be trusted; COMPLETE alone proves nothing (lab 04.1)."""
    problems = []
    scanned = statistics.get("numberOfDocumentsScanned", 0)
    if scanned != len(expected):
        problems.append(f"scanned {scanned} of {len(expected)} corpus files")
    if failed := statistics.get("numberOfDocumentsFailed", 0):
        problems.append(f"{failed} failed")
    not_indexed = [
        f"{name} ({documents.get(name, 'missing')})"
        for name in expected
        if documents.get(name) != "INDEXED"
    ]
    if not_indexed:
        problems.append(f"not indexed: {', '.join(not_indexed)}")
    return problems


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
