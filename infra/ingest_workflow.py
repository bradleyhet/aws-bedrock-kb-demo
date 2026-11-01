"""The ingest workflow: a Step Functions Standard state machine, written in JSONata.

It starts an ingestion job, polls it, lists the knowledge base's documents, and passes only when
the job scanned every corpus file, failed none, and every file is INDEXED. Each step is an AWS SDK
integration, so there is no Lambda. `${KnowledgeBaseId}` and `${DataSourceId}` are filled in by
CloudFormation's definition substitutions. `kb-demo ingest` starts it with the corpus file names:
{"corpus": ["access-control.md", ...]}.
"""

BEDROCK = "arn:aws:states:::aws-sdk:bedrockagent"
IDS = {"KnowledgeBaseId": "${KnowledgeBaseId}", "DataSourceId": "${DataSourceId}"}

# Each corpus file name -> its document status. The corpus keys have no prefix, so the last part
# of the S3 URI is the file name. A page holds at most 100 documents (the service rejects more);
# a bigger corpus would show up as missing files, so it could never pass by accident.
DOCUMENTS = """{%
  $count($states.result.DocumentDetails) = 0 ? {} :
  $merge($states.result.DocumentDetails.{ $split(Identifier.S3.Uri, '/')[-1]: Status })
%}"""

# Why the ingestion can't be trusted; an empty list means every corpus file is indexed. A job can
# be COMPLETE having indexed nothing. In JSONata any comparison with a missing value is
# false, so a missing count or document is tested for explicitly.
PROBLEMS = """{% (
  $stats := $job.Statistics;
  $files := $count($corpus);
  $scanned := $exists($stats.NumberOfDocumentsScanned) ? $stats.NumberOfDocumentsScanned : 0;
  $failed := $exists($stats.NumberOfDocumentsFailed) ? $stats.NumberOfDocumentsFailed : 0;
  $status := function($name) { $lookup($documents, $name) };
  $notIndexed := $filter($corpus, function($name) { $not($status($name) = 'INDEXED') });
  $label := function($name) {
    $name & ' (' & ($exists($status($name)) ? $status($name) : 'missing') & ')'
  };
  [
    $scanned != $files ? 'scanned ' & $scanned & ' of ' & $files & ' corpus files',
    $failed > 0 ? $failed & ' failed',
    $count($notIndexed) > 0 ? 'not indexed: ' & $join($map($notIndexed, $label), ', ')
  ]
) %}"""

JOB_FAILED = """{%
  'ingestion job ' & $jobId & ' is ' & $job.Status & ': ' &
  ($exists($job.FailureReasons) ? $join($job.FailureReasons, '; ') : 'no reason given')
%}"""

DEFINITION = {
    "Comment": "Ingest the kb-demo corpus, and pass only if every corpus file was indexed",
    "QueryLanguage": "JSONata",
    "TimeoutSeconds": 900,
    "StartAt": "StartIngestion",
    "States": {
        "StartIngestion": {
            "Type": "Task",
            "Resource": f"{BEDROCK}:startIngestionJob",
            "Arguments": IDS,
            "Assign": {
                "corpus": "{% $states.input.corpus %}",
                "jobId": "{% $states.result.IngestionJob.IngestionJobId %}",
            },
            "Next": "Wait",
        },
        "Wait": {"Type": "Wait", "Seconds": 15, "Next": "GetIngestion"},
        "GetIngestion": {
            "Type": "Task",
            "Resource": f"{BEDROCK}:getIngestionJob",
            "Arguments": {**IDS, "IngestionJobId": "{% $jobId %}"},
            "Assign": {"job": "{% $states.result.IngestionJob %}"},
            "Next": "Finished?",
        },
        "Finished?": {
            "Type": "Choice",
            "Choices": [
                {"Condition": "{% $job.Status = 'COMPLETE' %}", "Next": "ListDocuments"},
                {"Condition": "{% $job.Status in ['FAILED', 'STOPPED'] %}", "Next": "JobFailed"},
            ],
            "Default": "Wait",
        },
        "JobFailed": {"Type": "Fail", "Error": "IngestionJobFailed", "Cause": JOB_FAILED},
        "ListDocuments": {
            "Type": "Task",
            "Resource": f"{BEDROCK}:listKnowledgeBaseDocuments",
            "Arguments": {**IDS, "MaxResults": 100},
            "Assign": {"documents": DOCUMENTS},
            "Next": "Check",
        },
        "Check": {"Type": "Pass", "Assign": {"problems": PROBLEMS}, "Next": "Indexed?"},
        "Indexed?": {
            "Type": "Choice",
            "Choices": [{"Condition": "{% $count($problems) = 0 %}", "Next": "Indexed"}],
            "Default": "NotIndexed",
        },
        "Indexed": {
            "Type": "Succeed",
            "Output": {
                "ingestionJobId": "{% $jobId %}",
                "statistics": "{% $job.Statistics %}",
                "documents": "{% $documents %}",
            },
        },
        "NotIndexed": {
            "Type": "Fail",
            "Error": "CorpusNotIndexed",
            "Cause": "{% 'ingestion job ' & $jobId & ' did not index the corpus: '"
            " & $join($problems, '; ') %}",
        },
    },
}
