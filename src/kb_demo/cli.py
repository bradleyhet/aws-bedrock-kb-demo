"""kb-demo: set up, serve, inspect and tear down the knowledge base demo."""

import argparse
import logging
import sys
from collections.abc import Mapping

import boto3
import uvicorn

from kb_demo.config import Config
from kb_demo.resources import Clients, SetupError, Status, setup, status, teardown
from kb_demo.search import Searcher
from kb_demo.web import create_app

STATISTICS = {
    "numberOfDocumentsScanned": "scanned",
    "numberOfNewDocumentsIndexed": "new",
    "numberOfModifiedDocumentsIndexed": "modified",
    "numberOfDocumentsDeleted": "deleted",
    "numberOfDocumentsFailed": "failed",
}


def make_clients(config: Config) -> Clients:
    session = boto3.Session(profile_name=config.profile, region_name=config.region)
    return Clients(
        sts=session.client("sts", region_name=config.region),
        s3=session.client("s3", region_name=config.region),
        iam=session.client("iam", region_name=config.region),
        agent=session.client("bedrock-agent", region_name=config.region),
    )


def make_searcher(config: Config, knowledge_base_id: str) -> Searcher:
    session = boto3.Session(profile_name=config.profile, region_name=config.region)
    return Searcher(
        session.client("bedrock-agent-runtime", region_name=config.region),
        session.client("bedrock-runtime", region_name=config.region),
        knowledge_base_id=knowledge_base_id,
        model_id=config.model_id,
    )


def describe(statistics: Mapping[str, int]) -> str:
    return ", ".join(f"{label} {statistics.get(key, 0)}" for key, label in STATISTICS.items())


def print_status(found: Status, config: Config) -> None:
    kb, ds, job = found.knowledge_base, found.data_source, found.last_ingestion
    rows = {
        "bucket": found.bucket,
        "role": found.role,
        "knowledge base": kb and f"{kb.id} ({kb.status})",
        "data source": ds and f"{ds.id} ({ds.status})",
        "last ingestion": job and f"{job.status}: {describe(job.statistics)}",
        "model": config.model_id,
    }
    for label, value in rows.items():
        print(f"{label:<16}{value or 'not found'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kb-demo", description=__doc__)
    parser.add_argument("command", choices=["setup", "serve", "status", "teardown"])
    parser.add_argument("--port", type=int, default=8000, help="port for serve (default 8000)")
    args = parser.parse_args(argv)
    command = args.command

    logging.basicConfig(format="%(message)s")
    logging.getLogger("kb_demo").setLevel(logging.INFO)
    config = Config.from_env()
    clients = make_clients(config)

    if command == "setup":
        try:
            report = setup(clients, config)
        except SetupError as error:
            print(f"Setup failed: {error}", file=sys.stderr)
            return 1
        print(f"Knowledge base {report.knowledge_base_id}, data source {report.data_source_id}")
        print(f"Ingestion: {describe(report.statistics)}")
        for name, state in sorted(report.documents.items()):
            print(f"  {name:<24}{state}")
        return 0

    if command == "serve":
        kb = status(clients, config).knowledge_base
        searcher = None
        if kb and kb.status == "ACTIVE":
            searcher = make_searcher(config, kb.id)
            print(f"Knowledge base {kb.id}, model {config.model_id}")
        else:
            print("No ACTIVE knowledge base named kb-demo; the page will say to run setup first.")
        # Local only: the page has no authentication.
        uvicorn.run(create_app(searcher), host="127.0.0.1", port=args.port)
        return 0

    if command == "status":
        print_status(status(clients, config), config)
        return 0

    remaining = teardown(clients, config)
    print_status(remaining, config)
    return 0 if remaining == Status() else 1


if __name__ == "__main__":
    sys.exit(main())
