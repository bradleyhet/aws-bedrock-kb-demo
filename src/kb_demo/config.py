"""Settings: region, profile, resource names and the generation model."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

REPO_ROOT = Path(__file__).resolve().parents[2]
TAGS = {"Project": "kb-demo"}


@dataclass(frozen=True)
class Config:
    region: str = "ap-southeast-2"
    profile: str | None = "triage"  # None: the default credential chain, as in CI
    # Haiku 5.5's quota is 0 on this account (Support case 179221087300959); switch when it lands.
    model_id: str = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
    corpus_dir: Path = REPO_ROOT / "corpus"
    knowledge_base_name: str = "kb-demo"
    data_source_name: str = "kb-demo-s3"
    role_name: str = "kb-demo-kb-role"
    workflow_name: str = "kb-demo-ingest"
    api_url: str | None = None  # the deployed HTTP API; unset, the page calls Bedrock directly

    def bucket_name(self, account_id: str) -> str:
        return f"kb-demo-{account_id}-{self.region}"

    def workflow_arn(self, account_id: str) -> str:
        return f"arn:aws:states:{self.region}:{account_id}:stateMachine:{self.workflow_name}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Self:
        defaults = cls()
        return cls(
            profile=env.get("KB_DEMO_PROFILE", defaults.profile) or None,
            model_id=env.get("KB_DEMO_MODEL_ID", defaults.model_id),
            api_url=env.get("KB_DEMO_API_URL") or None,
        )
