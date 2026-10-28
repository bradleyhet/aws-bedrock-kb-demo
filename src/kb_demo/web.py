"""The local web page: a question box, an answer that cites its passages, and the passages."""

import re
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from kb_demo.samples import SAMPLES
from kb_demo.search import CITATION, Answer, Searcher

TEMPLATES = Jinja2Templates(directory=Path(__file__).parent / "templates")


def cite(answer: Answer) -> Markup:
    """The answer text, escaped, with each citation linked to its passage card."""

    def link(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number not in answer.citations:
            return match.group(0)
        return f'<a class="cite" href="#passage-{number}">[{number}]</a>'

    return Markup(CITATION.sub(link, str(escape(answer.text or ""))))


TEMPLATES.env.filters["cite"] = cite


def create_app(searcher: Searcher | None) -> FastAPI:
    app = FastAPI(title="kb-demo")

    def page(request: Request, status_code: int = 200, **context: object) -> HTMLResponse:
        context = {"samples": SAMPLES, "setup_needed": searcher is None, **context}
        return TEMPLATES.TemplateResponse(request, "index.html", context, status_code=status_code)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return page(request)

    @app.post("/ask", response_class=HTMLResponse)
    def ask(request: Request, question: Annotated[str, Form()] = "") -> HTMLResponse:
        if searcher is None:
            return page(request, 503)
        if not (question := question.strip()):
            return page(request, 400, message="Type a question first.")
        return page(request, question=question, answer=searcher.answer(question))

    return app
