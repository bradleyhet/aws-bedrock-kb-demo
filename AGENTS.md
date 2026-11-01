# AGENTS.md

A search-and-answer demo on Amazon Bedrock Managed Knowledge Base. A local web page answers
questions from a small fictional handbook in `corpus/`, and cites the passages it retrieved.
`PLAN.md` holds the platform facts, architecture, resource lifecycle and build order; read it
before starting a build step. `SESSION-HANDOFF.md` records where the last session stopped.

## Claims
- Report only measured numbers. Ingestion statistics, retrieval scores and costs come from a real
  run, and costs come from Cost Explorer filtered by the `Project=kb-demo` tag. Until a run
  exists, write "not yet measured".
- The corpus is fictional on purpose: its invented specifics (policy numbers, limits) mean a
  correct answer can only have come from retrieval.

## AWS
- Region `ap-southeast-2`. Local credentials come from `aws login --profile triage`
  (short-lived); access keys stay out of the project entirely.
- Pass `region_name` explicitly to every boto3 client. The generation model comes from
  `KB_DEMO_MODEL_ID`, passed explicitly to every Converse call.
- boto3 reads `aws login` credentials only with the CRT extra (`boto3[crt]`).
- In Git Bash, prefix AWS CLI calls with `MSYS_NO_PATHCONV=1`.
- Tag everything that takes tags with `Project=kb-demo`. Find resources by name, so setup and
  teardown stay idempotent and treat "not found" as success.
- An ingestion job reporting COMPLETE can still have indexed nothing. Setup passes only when
  scanned and indexed equal the corpus file count and failed is 0.

## Code
- Python 3.12 with uv. A change is done when `uv run ruff check` and `uv run pytest` are green.
- Inject clients, clocks and sleeps so tests run without AWS.
- Australian English in docs, comments, log messages and page text (organisation, prioritise,
  behaviour, licence).

## Repository
- Public GitHub repo `bradleyhet/aws-bedrock-kb-demo`. Run gitleaks before every push.
