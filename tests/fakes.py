"""In-memory stand-ins for the AWS services the demo calls.

Every call's parameters are checked against botocore's service model, so a misspelt or misplaced
parameter fails here the way it would against AWS. Statuses move on one step per poll, so the
code under test has to wait for them.
"""

import itertools
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import botocore.session
from botocore import xform_name
from botocore.exceptions import ClientError, ParamValidationError
from botocore.validate import ParamValidator

from kb_demo.config import Config
from kb_demo.handler import handle
from kb_demo.resources import Clients

_MODELS = botocore.session.get_session()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class _SinglePage:
    def __init__(self, method: Any) -> None:
        self._method = method

    def paginate(self, **params: Any) -> Any:
        yield self._method(**params)


class FakeService:
    service_name = ""

    def __init__(self, aws: "FakeAws") -> None:
        self.aws = aws
        self._model = _MODELS.get_service_model(self.service_name)
        self._operations = {xform_name(name): name for name in self._model.operation_names}
        self.errors: dict[str, str] = {}  # method name -> error code it raises instead of running

    def _call(self, method: str, params: dict[str, Any]) -> None:
        operation = self._model.operation_model(self._operations[method])
        report = ParamValidator().validate(params, operation.input_shape)
        if report.has_errors():
            raise ParamValidationError(report=report.generate_report())
        if method in self.errors:
            raise self._error(method, self.errors[method])

    def _error(self, method: str, code: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": code}}, self._operations[method])

    def _poll(self, table: dict[str, dict[str, Any]], key: str, method: str) -> dict[str, Any]:
        record = table.get(key)
        if record is None:
            raise self._error(method, "ResourceNotFoundException")
        if record["next"]:
            record["status"] = record["next"].pop(0)
        return record

    def get_paginator(self, method: str) -> _SinglePage:
        return _SinglePage(getattr(self, method))


class FakeSts(FakeService):
    service_name = "sts"

    def get_caller_identity(self, **params: Any) -> dict[str, Any]:
        self._call("get_caller_identity", params)
        return {"Account": self.aws.account_id, "Arn": f"arn:aws:iam::{self.aws.account_id}:user/t"}


class FakeS3(FakeService):
    service_name = "s3"

    def __init__(self, aws: "FakeAws") -> None:
        super().__init__(aws)
        self.buckets: dict[str, dict[str, bytes]] = {}

    def _bucket(self, method: str, name: str) -> dict[str, bytes]:
        if name not in self.buckets:
            raise self._error(method, "NoSuchBucket")
        return self.buckets[name]

    def head_bucket(self, **params: Any) -> dict[str, Any]:
        self._call("head_bucket", params)
        if params["Bucket"] not in self.buckets:
            raise self._error("head_bucket", "404")
        return {}

    def put_object(self, **params: Any) -> dict[str, Any]:
        self._call("put_object", params)
        self._bucket("put_object", params["Bucket"])[params["Key"]] = params["Body"]
        return {"ETag": '"0"'}

    def list_objects_v2(self, **params: Any) -> dict[str, Any]:
        self._call("list_objects_v2", params)
        keys = sorted(self._bucket("list_objects_v2", params["Bucket"]))
        page: dict[str, Any] = {"KeyCount": len(keys), "IsTruncated": False}
        if keys:  # like S3, an empty bucket's listing has no Contents at all
            page["Contents"] = [{"Key": key} for key in keys]
        return page

    def delete_objects(self, **params: Any) -> dict[str, Any]:
        self._call("delete_objects", params)
        bucket = self._bucket("delete_objects", params["Bucket"])
        for item in params["Delete"]["Objects"]:
            bucket.pop(item["Key"], None)
        return {}


class FakeIam(FakeService):
    service_name = "iam"

    def __init__(self, aws: "FakeAws") -> None:
        super().__init__(aws)
        self.roles: dict[str, str] = {}  # name -> ARN

    def get_role(self, **params: Any) -> dict[str, Any]:
        self._call("get_role", params)
        if params["RoleName"] not in self.roles:
            raise self._error("get_role", "NoSuchEntity")
        return {"Role": {"RoleName": params["RoleName"], "Arn": self.roles[params["RoleName"]]}}


class FakeAgent(FakeService):
    """bedrock-agent: managed knowledge bases, their data sources and ingestion jobs. The
    ingest workflow runs the jobs, so here a job only appears, finished, when FakeSfn runs."""

    service_name = "bedrock-agent"

    def __init__(self, aws: "FakeAws") -> None:
        super().__init__(aws)
        self.knowledge_bases: dict[str, dict[str, Any]] = {}
        self.data_sources: dict[str, dict[str, Any]] = {}
        self.jobs: dict[str, dict[str, Any]] = {}
        self._ids = itertools.count(1)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids):08d}"

    def _knowledge_base(self, method: str, kb_id: str) -> dict[str, Any]:
        if kb_id not in self.knowledge_bases:
            raise self._error(method, "ResourceNotFoundException")
        return self.knowledge_bases[kb_id]

    @staticmethod
    def _kb_view(record: dict[str, Any]) -> dict[str, Any]:
        return {key: record[key] for key in ("knowledgeBaseId", "name", "status", "roleArn")}

    @staticmethod
    def _ds_view(record: dict[str, Any]) -> dict[str, Any]:
        return {key: record[key] for key in ("knowledgeBaseId", "dataSourceId", "name", "status")}

    @staticmethod
    def _job_view(record: dict[str, Any]) -> dict[str, Any]:
        keys = ("knowledgeBaseId", "dataSourceId", "ingestionJobId", "status", "statistics")
        return {key: record[key] for key in keys}

    def create_knowledge_base(self, **params: Any) -> dict[str, Any]:
        self._call("create_knowledge_base", params)
        if params["knowledgeBaseConfiguration"]["type"] != "MANAGED":
            raise self._error("create_knowledge_base", "ValidationException")
        kb_id = self._new_id("KB")
        self.knowledge_bases[kb_id] = {
            "knowledgeBaseId": kb_id,
            "name": params["name"],
            "roleArn": params["roleArn"],
            "tags": params.get("tags", {}),
            "status": "CREATING",
            "next": ["CREATING", "ACTIVE"],
        }
        return {"knowledgeBase": self._kb_view(self.knowledge_bases[kb_id])}

    def list_knowledge_bases(self, **params: Any) -> dict[str, Any]:
        self._call("list_knowledge_bases", params)
        return {"knowledgeBaseSummaries": [self._kb_view(r) for r in self.knowledge_bases.values()]}

    def create_data_source(self, **params: Any) -> dict[str, Any]:
        self._call("create_data_source", params)
        self._knowledge_base("create_data_source", params["knowledgeBaseId"])
        configuration = params["dataSourceConfiguration"]
        if configuration["type"] != "MANAGED_KNOWLEDGE_BASE_CONNECTOR":
            raise self._error("create_data_source", "ValidationException")
        connector = configuration["managedKnowledgeBaseConnectorConfiguration"]
        ds_id = self._new_id("DS")
        self.data_sources[ds_id] = {
            "knowledgeBaseId": params["knowledgeBaseId"],
            "dataSourceId": ds_id,
            "name": params["name"],
            "bucket": connector["connectorParameters"]["connectionConfiguration"]["bucketName"],
            "status": "CREATING",
            "next": ["CREATING", "AVAILABLE"],
        }
        return {"dataSource": self._ds_view(self.data_sources[ds_id])}

    def list_data_sources(self, **params: Any) -> dict[str, Any]:
        self._call("list_data_sources", params)
        self._knowledge_base("list_data_sources", params["knowledgeBaseId"])
        sources = [
            self._ds_view(record)
            for record in self.data_sources.values()
            if record["knowledgeBaseId"] == params["knowledgeBaseId"]
        ]
        return {"dataSourceSummaries": sources}

    def ingested(self, kb_id: str, ds_id: str, statistics: dict[str, int]) -> str:
        """Record a finished ingestion job, as the workflow leaves one."""
        job_id = self._new_id("JOB")
        self.jobs[job_id] = {
            "knowledgeBaseId": kb_id,
            "dataSourceId": ds_id,
            "ingestionJobId": job_id,
            "statistics": statistics,
            "status": "COMPLETE",
            "next": [],
        }
        return job_id

    def list_ingestion_jobs(self, **params: Any) -> dict[str, Any]:
        self._call("list_ingestion_jobs", params)
        jobs = [
            self._job_view(record)
            for record in reversed(self.jobs.values())
            if record["dataSourceId"] == params["dataSourceId"]
        ]
        return {"ingestionJobSummaries": jobs[: params.get("maxResults", len(jobs))]}


class FakeSfn(FakeService):
    """stepfunctions: the deployed ingest workflow, scripted. The workflow's own check runs live
    (step 2.4: TestState and real executions); here every execution indexes the corpus it was
    given, unless a test sets how it ends instead."""

    service_name = "stepfunctions"
    STARTED = datetime(2026, 10, 30, 5, 0, tzinfo=UTC)
    TOOK = timedelta(seconds=75)

    def __init__(self, aws: "FakeAws") -> None:
        super().__init__(aws)
        self.state_machines: dict[str, tuple[str, str]] = {}  # ARN -> (knowledge base, source)
        self.executions: dict[str, dict[str, Any]] = {}
        self.inputs: list[dict[str, Any]] = []  # each execution's input
        self.ending: tuple[str, str | None, str | None] | None = None  # status, error, cause
        self._ids = itertools.count(1)

    def describe_state_machine(self, **params: Any) -> dict[str, Any]:
        self._call("describe_state_machine", params)
        arn = params["stateMachineArn"]
        if arn not in self.state_machines:
            raise self._error("describe_state_machine", "StateMachineDoesNotExist")
        return {"stateMachineArn": arn, "name": arn.rsplit(":", 1)[-1], "status": "ACTIVE"}

    def start_execution(self, **params: Any) -> dict[str, Any]:
        self._call("start_execution", params)
        machine = params["stateMachineArn"]
        if machine not in self.state_machines:
            raise self._error("start_execution", "StateMachineDoesNotExist")
        workflow_input = json.loads(params["input"])
        self.inputs.append(workflow_input)
        corpus = workflow_input["corpus"]
        statistics = {  # as the workflow returns them: Step Functions capitalises field names
            "NumberOfDocumentsScanned": len(corpus),
            "NumberOfNewDocumentsIndexed": len(corpus),
            "NumberOfModifiedDocumentsIndexed": 0,
            "NumberOfDocumentsDeleted": 0,
            "NumberOfDocumentsFailed": 0,
        }
        job_id = self.aws.agent.ingested(
            *self.state_machines[machine],
            {key[0].lower() + key[1:]: value for key, value in statistics.items()},
        )
        if self.ending:
            status, error, cause = self.ending
            end = {"error": error, "cause": cause}
        else:
            status = "SUCCEEDED"
            output = {
                "ingestionJobId": job_id,
                "statistics": statistics,
                "documents": dict.fromkeys(corpus, "INDEXED"),
            }
            end = {"output": json.dumps(output)}
        arn = f"{machine.replace(':stateMachine:', ':execution:')}:{next(self._ids):08d}"
        self.executions[arn] = {
            "executionArn": arn,
            "stateMachineArn": machine,
            "status": "RUNNING",
            "next": ["RUNNING", status],
            "startDate": self.STARTED,
            "end": {key: value for key, value in end.items() if value is not None},
        }
        return {"executionArn": arn, "startDate": self.STARTED}

    def describe_execution(self, **params: Any) -> dict[str, Any]:
        self._call("describe_execution", params)
        record = self._poll(self.executions, params["executionArn"], "describe_execution")
        view = {key: record[key] for key in ("executionArn", "stateMachineArn", "status")}
        view["startDate"] = record["startDate"]
        if record["status"] != "RUNNING":
            view |= {"stopDate": record["startDate"] + self.TOOK, **record["end"]}
        return view


def retrieval_result(
    file: str, score: float, text: str, *, with_location: bool = True
) -> dict[str, Any]:
    """One Retrieve result, shaped like those a managed knowledge base returned in step 5."""
    uri = f"https://kb-demo-bucket.s3.ap-southeast-2.amazonaws.com/{file}"
    result: dict[str, Any] = {
        "content": {"text": text, "type": "TEXT"},
        "score": score,
        "documentId": f"s3://kb-demo-bucket/{file}",
        "metadata": {"_source_uri": uri, "_document_title": file, "_data_source_type": "S3"},
    }
    if with_location:
        result["location"] = {"type": "S3", "s3Location": {"uri": uri}}
    return result


class FakeRuntime(FakeService):
    """bedrock-agent-runtime: Retrieve returns whatever the test put in results."""

    service_name = "bedrock-agent-runtime"

    def __init__(self, aws: "FakeAws") -> None:
        super().__init__(aws)
        self.results: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []

    def retrieve(self, **params: Any) -> dict[str, Any]:
        self._call("retrieve", params)
        self.requests.append(params)
        limit = params["retrievalConfiguration"]["managedSearchConfiguration"]["numberOfResults"]
        return {"retrievalResults": self.results[:limit]}


class FakeBedrock(FakeService):
    """bedrock-runtime: Converse replies with whatever the test put in reply."""

    service_name = "bedrock-runtime"

    def __init__(self, aws: "FakeAws") -> None:
        super().__init__(aws)
        self.reply = "The handbook doesn't cover that."
        self.requests: list[dict[str, Any]] = []

    def converse(self, **params: Any) -> dict[str, Any]:
        self._call("converse", params)
        self.requests.append(params)
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": self.reply}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
        }


class FakeApi:
    """The deployed HTTP API: it runs the real handler, or refuses as the test says."""

    def __init__(self, searcher: Any) -> None:
        self.searcher = searcher
        self.requests: list[Any] = []
        self.refuse: tuple[int, bytes] | None = None  # (status, body) to answer every request with

    def send(self, request: Any) -> tuple[int, bytes]:
        self.requests.append(request)
        if self.refuse:
            return self.refuse
        event = {"version": "2.0", "body": request.body.decode(), "isBase64Encoded": False}
        response = handle(event, self.searcher)
        return response["statusCode"], response["body"].encode()


class FakeAws:
    def __init__(self, account_id: str = "111122223333", region: str = "ap-southeast-2") -> None:
        self.account_id = account_id
        self.region = region
        self.sts = FakeSts(self)
        self.s3 = FakeS3(self)
        self.iam = FakeIam(self)
        self.agent = FakeAgent(self)
        self.sfn = FakeSfn(self)
        self.runtime = FakeRuntime(self)
        self.bedrock = FakeBedrock(self)

    def clients(self) -> Clients:
        return Clients(sts=self.sts, s3=self.s3, iam=self.iam, agent=self.agent, sfn=self.sfn)

    def deploy(self, config: Config) -> None:
        """What cdk deploy leaves: an empty bucket, the role, an ACTIVE knowledge base, an
        AVAILABLE data source that reads the bucket, and the ingest workflow over them."""
        bucket = config.bucket_name(self.account_id)
        self.s3.buckets[bucket] = {}
        role_arn = f"arn:aws:iam::{self.account_id}:role/{config.role_name}"
        self.iam.roles[config.role_name] = role_arn
        kb_id = self.agent.create_knowledge_base(
            name=config.knowledge_base_name,
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "MANAGED",
                "managedKnowledgeBaseConfiguration": {"embeddingModelType": "MANAGED"},
            },
        )["knowledgeBase"]["knowledgeBaseId"]
        connection = {"bucketName": bucket, "bucketOwnerAccountId": self.account_id}
        ds_id = self.agent.create_data_source(
            knowledgeBaseId=kb_id,
            name=config.data_source_name,
            dataSourceConfiguration={
                "type": "MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                "managedKnowledgeBaseConnectorConfiguration": {
                    "connectorParameters": {"connectionConfiguration": connection}
                },
            },
        )["dataSource"]["dataSourceId"]
        # CloudFormation returns only once both have settled.
        self.agent.knowledge_bases[kb_id].update(status="ACTIVE", next=[])
        self.agent.data_sources[ds_id].update(status="AVAILABLE", next=[])
        self.sfn.state_machines[config.workflow_arn(self.account_id)] = (kb_id, ds_id)
