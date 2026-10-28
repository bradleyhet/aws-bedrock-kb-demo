# kb-demo: handbook search on Amazon Bedrock Managed Knowledge Base

Ask a question on a local web page and get an answer drawn from a small staff handbook. Each claim
in the answer cites a numbered passage, and the passages behind it are listed underneath with
their retrieval scores. One command creates the AWS resources and another removes them.

The handbook is fictional. It belongs to Acme Field Services, an invented company, and its policies
are full of invented specifics: a hotel limit of A$255 a night in Perth, four hours to log a near
miss, an excess of A$250 on a second laptop claim. A correct answer can only have come from retrieval, not from
anything the model already knew.

## Why not Amazon Kendra

The demo was first planned on Kendra. Kendra went into maintenance mode on 30 June 2026 and stopped
taking new customers on 30 July 2026
([Kendra availability change](https://docs.aws.amazon.com/kendra/latest/dg/kendra-availability-change.html)),
and AWS names Bedrock Managed Knowledge Base as its replacement. A managed knowledge base looks
after parsing, chunking, embeddings, the index and reranking itself, and it has no hourly charge,
where a Kendra GenAI index is billed by the hour.

## How it works

```
corpus/*.md ──(kb-demo setup)──> S3 bucket ──> managed data source ──ingest──> managed index
                                                                                   │
browser ──> FastAPI on 127.0.0.1:8000 ── Retrieve ──> top 5 passages (file, score, text)
                                     └── Converse ──> answer citing [1]..[n]
```

- **Setup** creates, or finds by name, an S3 bucket for the corpus, an IAM role the knowledge base
  uses to read it, a knowledge base of type `MANAGED` with managed embeddings, and a data source
  using the managed S3 connector. It uploads `corpus/`, deletes objects no longer there, and runs
  an ingestion job. Everything that takes tags is tagged `Project=kb-demo`, and rerunning setup is
  safe.
- **Ingestion is checked, not trusted.** A job can report COMPLETE having indexed nothing, and its
  new-document count reads 0 whenever an unchanged corpus is re-synced. So setup lists the
  documents in the knowledge base afterwards, and fails unless every corpus file is INDEXED, the
  job scanned as many documents as there are files, and none failed.
- **Two calls, not RetrieveAndGenerate.** The page calls Retrieve for the top five passages, then
  Converse with those passages numbered in the prompt. The system prompt tells the model to answer
  only from the passages, to treat them as data rather than instructions, to cite them as `[n]`,
  and to reply "The handbook doesn't cover that." when they don't answer the question. Keeping the
  prompt in our hands is the point of the second call.
- **Search still works without the model.** If Converse fails (no model access, throttling, a bad
  model ID), the page shows the passages with a notice saying why there is no answer.
- The page has no JavaScript, and its light and dark themes follow the system setting.

## Run it

You need Python 3.12, [uv](https://docs.astral.sh/uv/), AWS CLI v2, and an AWS account in a region
that has Bedrock Managed Knowledge Base (this one uses `ap-southeast-2`) with access to the
generation model.

```bash
aws login --profile triage   # short-lived credentials; no access keys anywhere
uv sync
uv run kb-demo setup         # create or update the resources and ingest the corpus
uv run kb-demo serve         # then open http://127.0.0.1:8000
uv run kb-demo status        # what exists, the last ingestion, the model in use
uv run kb-demo teardown      # delete everything setup made
```

boto3 reads `aws login` credentials only with its CRT extra, which is why the dependency is
`boto3[crt]`.

| Environment variable | Default | Purpose |
|---|---|---|
| `KB_DEMO_PROFILE` | `triage` | AWS CLI profile |
| `KB_DEMO_MODEL_ID` | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | Model for Converse |

The default model is Claude Haiku 4.5 because Haiku 5.5's quota on the account this was built on is
still 0. Once it isn't, set `KB_DEMO_MODEL_ID=au.anthropic.claude-haiku-5-5`.

## Measured results

Measured on 28 October 2026 against the real account, with Haiku 4.5:

- **First setup:** 2 min 32 s. The job scanned 8 documents, indexed 8 as new, and failed none;
  all 8 files were INDEXED.
- **Rerun:** 67 s with the same knowledge base. The job scanned 8, indexed 0 as new and 8 as
  modified (setup re-uploads every file), and failed none.
- **Retrieve** ranks the expected file first for each sample question:

  | Sample question | Expected file | Its score | Best other file |
  |---|---|---|---|
  | What's the most I can spend on a hotel night in Perth? | `travel.md` | 0.77 | 0.33 |
  | How soon after a near miss do I have to log it? | `incident-reporting.md` | 0.85 | 0.25 |
  | I've cracked my laptop screen for the second time. Will it cost me anything? | `laptop-replacement.md` | 0.79 | 0.33 |

- **Through the page,** each sample question was answered with its expected fact and cited only
  its expected file. "How many weeks of parental leave do I get?", which the handbook doesn't
  cover, got "The handbook doesn't cover that." With a bad model ID, Converse raised
  `ValidationException` and the page showed the notice and all five passages.

## Cost

The managed knowledge base's list prices as of 28 October 2026
([Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)):

- storage, US$5 per GB of raw data a month (the corpus is about 13 KB);
- Retrieve, US$1 per 1,000 calls;
- parsing, managed embeddings and the managed reranker, no charge;
- no hourly charge, so storage is the only cost while the demo sits idle.

Converse is billed per token at the model's price, and S3 storage for a corpus this size is
negligible.

**Measured cost: not yet measured.** To measure it, activate `Project` as a cost allocation tag
(Billing and Cost Management, Cost allocation tags; it only applies from activation onwards), then
filter Cost Explorer by `Project = kb-demo` once a day of data exists. Model costs are tagged
through an application inference profile
([inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles.html)),
and this demo calls the system-defined cross-Region profile directly, so Converse costs appear
under Bedrock model usage rather than under the tag.

## Tear it down

```bash
uv run kb-demo teardown
```

Teardown deletes the data source, waits for it to go, deletes the knowledge base and waits again,
then empties and deletes the bucket and deletes the role. It finishes by printing the status, where
every resource should read "not found", and exits with 1 if anything remains. It also copes with an
account that a failed setup left half-built.

## Tests

```bash
uv run ruff check
uv run pytest
```

The tests make no AWS calls. In-memory fakes stand in for S3, IAM, `bedrock-agent`,
`bedrock-agent-runtime` and `bedrock-runtime`, and they check every call's parameters against
botocore's service models, so a misspelt or misplaced parameter fails in a test the way it would
against AWS. Setup, teardown and status are tested end to end over the fakes, as are the search
and the web page.

## What this doesn't show

- **Scale.** Eight files, about 13 KB. Nothing here says how retrieval quality or ingestion time
  hold up over thousands of documents.
- **Access control.** The page has no sign-in and listens on 127.0.0.1 only. Everyone who can reach
  it sees every document; there is no per-user filtering.
- **Other connectors.** Only S3. Managed knowledge bases also connect to SharePoint, Confluence,
  Google Drive, Salesforce and others.
- **Evaluation.** Three sample questions and one off-corpus question, checked by script. There is
  no evaluation set or scoring.
- **Conversation.** Each question stands alone, with no follow-ups and no streaming.
- **Infrastructure as code.** Setup and teardown are boto3, because no CloudFormation or CDK
  resource for managed knowledge bases was found.

## Layout

```
corpus/                     the fictional handbook, one Markdown file per policy
src/kb_demo/config.py       region, profile, resource names, model
src/kb_demo/resources.py    setup, status and teardown
src/kb_demo/search.py       Retrieve, then Converse; passages, answers, citations
src/kb_demo/web.py          the FastAPI app; templates/index.html is the page
src/kb_demo/samples.py      the three sample questions and the fact each depends on
src/kb_demo/cli.py          the kb-demo command
tests/                      unit tests over in-memory AWS fakes
```
