"""Ask an API instead of Bedrock: POST /ask, signed with SigV4 for the deployed API's IAM
authorisation, or unsigned for a local container."""

import json
from collections.abc import Callable
from typing import Any

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSPreparedRequest, AWSRequest
from botocore.httpsession import URLLib3Session

from kb_demo.search import Answer

Send = Callable[[AWSPreparedRequest], tuple[int, bytes]]


class ApiError(Exception):
    pass


def send(request: AWSPreparedRequest) -> tuple[int, bytes]:
    response = URLLib3Session(timeout=35).send(request)  # the API gives up at 30 s
    return response.status_code, response.content


class ApiSearcher:
    def __init__(
        self, url: str, credentials: Any | None, *, region: str, send: Send = send
    ) -> None:
        """No credentials means unsigned requests: for a container on a CI runner, not the API."""
        self._url = url.rstrip("/") + "/ask"
        self._credentials = credentials
        self._region = region
        self._send = send

    def answer(self, question: str) -> Answer:
        request = AWSRequest(
            method="POST",
            url=self._url,
            data=json.dumps({"question": question}).encode(),
            headers={"Content-Type": "application/json"},
        )
        if self._credentials is not None:
            SigV4Auth(self._credentials, "execute-api", self._region).add_auth(request)
        status, body = self._send(request.prepare())
        if status != 200:
            raise ApiError(f"The API answered {status}: {_reason(body)}")
        return Answer.from_json(json.loads(body))


def _reason(body: bytes) -> str:
    text = body.decode(errors="replace")
    try:
        return json.loads(text)["message"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return text[:200]
