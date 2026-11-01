# kb-demo: handbook search on Amazon Bedrock Managed Knowledge Base

Ask a question on a local web page and get an answer drawn from a small staff handbook. Each claim
in the answer cites a numbered passage, and the passages behind it are listed underneath with
their retrieval scores. `cdk deploy` creates the AWS resources, `kb-demo ingest` loads the
handbook, and `cdk destroy` removes it all.

Around that sits a small serverless service, built and measured a step at a time: the same search
behind an IAM-authorised HTTP API on Lambda, ingestion as a Step Functions workflow that won't pass
until every file is indexed, the whole stack in one CDK app, and releases from GitHub Actions that
sign in to AWS by OIDC and deploy only once an evaluation gate passes.

The handbook is fictional. It belongs to Acme Field Services, an invented company, and its policies
are full of invented specifics: a hotel limit of A$255 a night in Perth, four hours to log a near
miss, an excess of A$250 on a second laptop claim. A correct answer can only have come from
retrieval, not from anything the model already knew.

## Why not Amazon Kendra

The demo was first planned on Kendra. Kendra went into maintenance mode on 30 June 2026 and stopped
taking new customers on 30 July 2026
([Kendra availability change](https://docs.aws.amazon.com/kendra/latest/dg/kendra-availability-change.html)),
and AWS names Bedrock Managed Knowledge Base as its replacement. A managed knowledge base looks
after parsing, chunking, embeddings, the index and reranking itself, and it has no hourly charge,
where a Kendra GenAI index is billed by the hour.

## How it works

```
corpus/*.md ──(kb-demo ingest)──> S3 bucket ──> managed data source ──ingest──> managed index
                  └──> Step Functions: start the job ──> poll ──> list documents ──> all INDEXED?
                                                                                   │
browser ──> FastAPI on 127.0.0.1:8000 ── Retrieve ──> top 5 passages (file, score, text)
                                     └── Converse ──> answer citing [1]..[n]

optionally, with KB_DEMO_API_URL set:
browser ──> FastAPI on 127.0.0.1 ──SigV4──> HTTP API (IAM auth) ──> Lambda ──> Retrieve, Converse
```

- **One CDK stack** (`infra/app.py`) holds everything: an S3 bucket for the corpus, an IAM role the
  knowledge base uses to read it, a knowledge base of type `MANAGED` with managed embeddings, a
  data source using the managed S3 connector, the ingest workflow, and the API below. Everything
  that takes tags is tagged `Project=kb-demo`.
- **Ingest** uploads `corpus/` to the stack's bucket, deletes objects no longer there, then starts
  the ingest workflow with the corpus file names and waits for it to end. Rerunning it is safe.
- **Ingestion is checked, not trusted.** A job can report COMPLETE having indexed nothing, and its
  new-document count reads 0 whenever an unchanged corpus is re-synced. So the workflow lists the
  documents in the knowledge base afterwards, and fails unless every corpus file is INDEXED, the
  job scanned as many documents as there are files, and none failed.
- **The workflow is Step Functions, with no Lambda.** A Standard state machine written in JSONata
  (`infra/ingest_workflow.py`) calls Bedrock through Step Functions' AWS SDK integrations: it
  starts an ingestion job, reads it every 15 seconds until it ends, lists the documents, and then
  checks them. It gives up after 15 minutes. A failed check fails the execution with
  `CorpusNotIndexed` and a cause that says what was wrong, such as
  `scanned 8 of 9 corpus files; not indexed: ghost.md (missing)`, and a FAILED or STOPPED job fails
  it with `IngestionJobFailed` and the job's own reasons. Its role can start, read and list
  ingestion on this one knowledge base and nothing else.
- **Two calls, not RetrieveAndGenerate.** The page calls Retrieve for the top five passages, then
  Converse with those passages numbered in the prompt. The system prompt tells the model to answer
  only from the passages, to treat them as data rather than instructions, to cite them as `[n]`,
  and to reply "The handbook doesn't cover that." when they don't answer the question. Keeping the
  prompt in our hands is the point of the second call.
- **Search still works without the model.** If Converse fails (no model access, throttling, a bad
  model ID), the page shows the passages with a notice saying why there is no answer.
- **The same search, deployed.** The stack puts the search behind `POST /ask` on an API Gateway
  HTTP API. The route requires IAM authorisation, so every call must be SigV4-signed by an identity
  allowed to invoke it, and the stage is throttled to 2 requests a second (burst 5). The Lambda's
  role can call Retrieve on this one knowledge base and InvokeModel on this one model's inference
  profile and the model in the two Regions the profile routes to, and write to its own log group;
  nothing else. With `KB_DEMO_API_URL` set, `kb-demo serve` signs its calls with your profile and
  asks the API instead of Bedrock.
- The page has no JavaScript, and its light and dark themes follow the system setting.

## Run it

You need Python 3.12, [uv](https://docs.astral.sh/uv/), AWS CLI v2, Node.js for the CDK CLI, and
an AWS account in a region that has Bedrock Managed Knowledge Base (this one uses `ap-southeast-2`),
with access to the generation model and CDK bootstrapped.

```bash
aws login --profile triage   # short-lived credentials; no access keys anywhere
uv sync
bunx aws-cdk@2.1146.1 deploy kb-demo --profile triage   # create or update it (npx works too)
uv run kb-demo ingest        # upload the corpus, then run the ingest workflow over it
uv run kb-demo serve         # then open http://127.0.0.1:8000
uv run kb-demo status        # what exists, the workflow, the last ingestion, the model
uv run kb-demo eval          # the release gate: sample questions and an off-corpus one
```

boto3 reads `aws login` credentials only with its CRT extra, which is why the dependency is
`boto3[crt]`.

| Environment variable | Default | Purpose |
|---|---|---|
| `KB_DEMO_PROFILE` | `triage` | AWS CLI profile |
| `KB_DEMO_MODEL_ID` | `au.anthropic.claude-haiku-4-5-20251001-v1:0` | Model for Converse |
| `KB_DEMO_API_URL` | unset | The deployed API; when set, the page asks it instead of Bedrock |

The default model is Claude Haiku 4.5 because Haiku 5.5's quota on the account this was built on is
still 0. Once it isn't, set `KB_DEMO_MODEL_ID=au.anthropic.claude-haiku-5-5`.

To have the page ask the deployed API instead of Bedrock, pass the stack's `ApiUrl` output:

```bash
KB_DEMO_API_URL=<ApiUrl output> uv run kb-demo serve
```

The python3.12 Lambda runtime's own boto3 is too old for managed knowledge bases, so the function
gets the boto3 and botocore versions `uv.lock` pins, in a layer that `uv` builds at synth time.

## Release pipeline

`.github/workflows/ci.yml` runs ruff, pytest and gitleaks on every push. A `v*` tag also runs the
evaluation gate and, only if it passes, `cdk deploy kb-demo`:

```
push ──> ruff, pytest, gitleaks
v* tag ──> the same ──> kb-demo eval (this commit's prompt, live knowledge base) ──> cdk deploy
```

- **The gate** (`kb-demo eval`) asks the three sample questions and one the handbook doesn't
  cover, using the tagged commit's own search code and prompt against the live knowledge base. It
  fails unless each sample answer cites its expected file and states its fact, and the off-corpus
  question gets "The handbook doesn't cover that." So a release that breaks the prompt never
  deploys.
- **No AWS keys in GitHub.** The jobs sign in by OpenID Connect. A separate `kb-demo-ci` stack,
  deployed once by hand, holds the GitHub OIDC provider and two roles. Both trust only
  `repo:bradleyhet@36786625/aws-bedrock-kb-demo@1439180417:ref:refs/tags/v*` with audience
  `sts.amazonaws.com`. The repo has GitHub's immutable subject claims on, so the subject names
  the owner and repo by ID as well as by name, and a renamed or re-created repo can't match:
  - `kb-demo-ci-evaluate` can list knowledge bases, call Retrieve on knowledge bases tagged
    `Project=kb-demo`, and invoke the model;
  - `kb-demo-ci-deploy` can only assume the CDK bootstrap roles, which do the deploying.

  Keeping them out of the demo stack means `cdk destroy kb-demo` never locks CI out.

```bash
bunx aws-cdk@2.1146.1 deploy kb-demo-ci --profile triage   # once, before the first release
gh secret set AWS_ACCOUNT --body <account ID>   # a secret, not a variable, so the logs mask it
```

## Measured results

Measured on 28 October 2026 against the real account, with Haiku 4.5, using the boto3 `setup` and
`teardown` commands that the CDK stack has since replaced:

- **First setup:** 2 min 32 s. The job scanned 8 documents, indexed 8 as new, and failed none;
  all 8 files were INDEXED.
- **Rerun:** 67 s with the same knowledge base. The job scanned 8, indexed 0 as new and 8 as
  modified (setup re-uploads every file), and failed none.
- **Teardown:** 11 min 36 s, about 9 minutes of it deleting the data source. The AWS CLI then
  showed no knowledge base, bucket or role left, and a setup from scratch rebuilt everything in
  2 min 32 s (scanned 8, new 8, failed 0).
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

- **The API,** measured on 30 October 2026:
  - The first `cdk deploy` took 78.6 s, 58.7 s of it CloudFormation.
  - The python3.12 runtime's bundled boto3 was 1.42.97, which rejected `managedSearchConfiguration`
    with a ParamValidationError. With boto3 1.43.120 in the layer, it works.
  - An unsigned `POST /ask` got 403 `{"message":"Forbidden"}`.
  - Signed, each sample question cited its expected file, and "What is the boiling point of
    mercury?" got "The handbook doesn't cover that."
  - Latency, from Lambda's REPORT lines and from the client on the build machine:

    | Call | Lambda | Round trip |
    |---|---|---|
    | Cold (first after deploy) | 758 ms init + 1,865 ms | 3.24 s |
    | Warm, 8 calls | median 962 ms (902 ms to 4,415 ms) | median 1.31 s (1.25 s to 4.77 s) |

- **The whole stack in CDK,** measured on 30 October 2026, after the boto3-made resources were torn
  down so CloudFormation could take their names:
  - `cdk deploy` from scratch took 99.3 s, 85.0 s of it CloudFormation. `kb-demo ingest` then took
    128 s: scanned 8, new 8, failed 0, and all 8 files INDEXED.
  - `cdk destroy` took 12 min 8 s, most of it deleting the data source. Afterwards nothing tagged
    `Project=kb-demo` remained, and no `kb-demo` knowledge base, bucket or IAM role.
  - One log group did remain: the one CDK's bucket auto-delete handler had made for itself, which
    CloudFormation didn't own. The stack now gives that handler a log group of its own, so
    `cdk destroy` deletes it. The redeploy showed the handler writing there; a second destroy
    hasn't been run to confirm it.
  - After the rebuild, an unsigned call got 403, each sample question cited its expected file, and
    the off-corpus question was declined.

- **The release pipeline,** measured on 30 October 2026:
  - The first release, `v0.2.0`, failed its first attempt at the gate's sign-in. GitHub's tokens
    for this repo carry the immutable subject, with the owner and repo IDs, but the roles trusted
    only the name form, so STS denied AssumeRoleWithWebIdentity and the deploy was skipped. With
    the trust condition corrected and `kb-demo-ci` redeployed (41.5 s), the second attempt passed.
    The gate passed all 4 checks in an 18 s job, and `cdk deploy kb-demo` from GitHub took a 64 s
    job (synthesis 11.3 s, deployment 19.4 s), which updated the function's code.
  - After the release, an unsigned call got 403, each sample question cited its expected file, and
    the off-corpus question was declined. Round trip: cold 2.48 s; 8 warm calls median 1.26 s
    (1.20 s to 1.87 s).
  - The drill, `v0.2.0-drill.1`, tagged a commit whose prompt says
    "Answer in plain sentences, without passage numbers or square brackets." ruff, pytest and
    gitleaks passed. The gate failed with 1 of 4 checks passed: each sample answer cited nothing,
    and only the off-corpus question, still declined, passed. The deploy job was skipped, and the
    function kept the code the release had deployed.
  - The Actions runs behind these figures, and behind `v0.3.0`'s below, printed the AWS account ID
    in their logs, so they were deleted on 1 November 2026. CI now reads the ID from a repo secret,
    which the logs mask.

- **The ingest workflow,** measured on 30 October 2026:
  - Step Functions' validator recognised the three `bedrockagent` SDK actions the workflow uses
    and rejected a made-up one, so no Lambda was needed.
  - The check state, run through TestState before any deploy, failed all five broken cases, in the
    wording of the Python check it replaces: a job reporting COMPLETE that indexed nothing
    (`scanned 0 of 2 corpus files; not indexed: a.md (missing), b.md (missing)`), one failed
    document, a file missing from the index, two unindexed files, and absent statistics. A fully
    indexed corpus passed.
  - TestState on the listing step, with the workflow's own role against the live knowledge base,
    found that ListKnowledgeBaseDocuments accepts at most 100 results a page; the first draft asked
    for 1,000 and got `ValidationException`.
  - `cdk deploy` adding the workflow took 64.2 s.
  - A passing run, `uv run kb-demo ingest`: execution
    `kb-demo-ingest:f20f741a-2a9f-46fc-af70-3e4fdfb9be70`
    SUCCEEDED in 123.8 s (8 polls, 29 states), scanned 8, modified 8, failed 0, all 8 files
    INDEXED. The command took 2 min 16 s in all, including the upload.
  - A forced failure: the same workflow started with the real corpus plus `ghost.md`, which isn't
    in the bucket. Execution
    `kb-demo-ingest:forced-failure-ghost-file`
    FAILED in 62.7 s with `CorpusNotIndexed`:
    "ingestion job VHJH2DHCDW did not index the corpus: scanned 8 of 9 corpus files; not indexed:
    ghost.md (missing)".
  - Released as `v0.3.0`: the gate passed 4 of 4 checks in a 19 s job, and `cdk deploy` from
    GitHub found only CDK's metadata to update (deployment 10.2 s), so the stack deployed by hand
    already matched the tagged commit.

## Cost

The managed knowledge base's list prices as of 28 October 2026
([Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/)):

- storage, US$5 per GB of raw data a month (the corpus is about 13 KB);
- Retrieve, US$1 per 1,000 calls;
- parsing, managed embeddings and the managed reranker, no charge;
- no hourly charge, so storage is the only cost while the demo sits idle.

Converse is billed per token at the model's price, and S3 storage for a corpus this size is
negligible.

**Measured cost: not yet measured.** `Project` was activated as a cost allocation tag on
30 October 2026, and a backfill from 1 October (so usage since the first setup counts) finished the
same day. Cost Explorer still showed no line item carrying the tag that day; once one does, the
figure comes from Cost Explorer filtered by `Project = kb-demo`. Model costs are tagged through an
application inference profile
([inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles.html)),
and this demo calls the system-defined cross-Region profile directly, so Converse costs appear
under Bedrock model usage rather than under the tag.

## Tear it down

```bash
bunx aws-cdk@2.1146.1 destroy kb-demo --profile triage --force
uv run kb-demo status        # every resource should read "not found"
```

CloudFormation deletes the API, the ingest workflow, the data source, the knowledge base, the
roles and the bucket, which the auto-delete handler empties first. Expect it to take around 12
minutes, most of it waiting for the data source. It leaves the account's CDK bootstrap stack
(`CDKToolkit`) and the `kb-demo-ci` stack in place; `cdk destroy kb-demo-ci` removes the latter.

## Tests

```bash
uv run ruff check
uv run pytest
```

The tests make no AWS calls. In-memory fakes stand in for S3, IAM, `bedrock-agent`,
`stepfunctions`, `bedrock-agent-runtime` and `bedrock-runtime`, and they check every call's
parameters against botocore's service models, so a misspelt or misplaced parameter fails in a test
the way it would against AWS. Ingest and status are tested over fakes set up the way `cdk deploy`
leaves the account, as are the search and the web page. The workflow is a scripted fake there, so
its check is not unit-tested: it was run through Step Functions' TestState API against six cases,
and against the live knowledge base (see Measured results). The Lambda handler is tested on HTTP
API events, and the signing client is tested against a fake API that checks its SigV4 headers and
runs the real handler. The evaluation gate is tested on scripted answers, one for each way an
answer can fail.

## What this doesn't show

- **Scale.** Eight files, about 13 KB. Nothing here says how retrieval quality or ingestion time
  hold up over thousands of documents.
- **Access control.** The page has no sign-in and listens on 127.0.0.1 only. The API admits only
  signed callers with permission to invoke it, but whoever it admits sees every document; there is
  no per-user filtering.
- **Other connectors.** Only S3. Managed knowledge bases also connect to SharePoint, Confluence,
  Google Drive, Salesforce and others.
- **Evaluation.** Three sample questions and one off-corpus question, checked by script. There is
  no evaluation set or scoring.
- **Conversation.** Each question stands alone, with no follow-ups and no streaming.
- **Triggers.** The workflow runs when `kb-demo ingest` starts it. Nothing starts it on an S3
  upload or a schedule.
- **Release safety beyond the gate.** There is one environment, with no staging, canary or
  automatic rollback. The gate tests a release's code and prompt against the knowledge base already
  deployed, so a change to the stack itself deploys without being evaluated first. CI deploys only
  `kb-demo`; `kb-demo-ci` is deployed by hand.
- **Operations.** No alarms, dashboards or tracing. The Lambda logs to CloudWatch Logs, kept for a
  week, and Step Functions keeps each execution's history.

## Layout

```
corpus/                     the fictional handbook, one Markdown file per policy
src/kb_demo/config.py       region, profile, resource names, model
src/kb_demo/resources.py    ingest and status
src/kb_demo/search.py       Retrieve, then Converse; passages, answers, citations
src/kb_demo/web.py          the FastAPI app; templates/index.html is the page
src/kb_demo/samples.py      the sample questions, the fact each depends on, the off-corpus one
src/kb_demo/evaluate.py     the release gate behind kb-demo eval
src/kb_demo/cli.py          the kb-demo command
src/kb_demo/handler.py      the Lambda handler behind POST /ask
src/kb_demo/remote.py       the SigV4-signing client the page uses when KB_DEMO_API_URL is set
infra/app.py, cdk.json      the CDK app: the kb-demo stack, and kb-demo-ci for GitHub's OIDC roles
infra/ingest_workflow.py    the ingest workflow: a JSONata state machine, SDK integrations only
.github/workflows/ci.yml    checks on every push; gate, then deploy, on a v* tag
tests/                      unit tests over in-memory AWS fakes
```
