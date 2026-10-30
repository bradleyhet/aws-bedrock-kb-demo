"""CDK app: the whole demo as one stack.

The corpus bucket, the knowledge base and its data source, POST /ask on an HTTP API that
requires IAM authorisation, answered by a Lambda, and the ingest workflow in Step Functions. After
cdk deploy, `kb-demo ingest` uploads the corpus and runs the workflow over it.
"""

import json
import os
import subprocess
from importlib.metadata import version
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import jsii
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk.aws_apigatewayv2_authorizers import HttpIamAuthorizer
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct
from ingest_workflow import DEFINITION

from kb_demo.config import REPO_ROOT, TAGS, Config

# The Regions the au. inference profile routes to (get-inference-profile, 2026-10-30). Converse
# through a profile needs InvokeModel on the profile and on the model in each of them.
PROFILE_REGIONS = ("ap-southeast-2", "ap-southeast-4")
FUNCTION_NAME = "kb-demo-ask"
# The repo has GitHub's immutable subject claims on (actions/oidc/customization/sub), so its
# tokens name it by owner and repo ID as well: a renamed or re-created repo can't match.
GITHUB_SUBJECT = "repo:bradleyhet@36786625/aws-bedrock-kb-demo@1439180417"
# The python3.12 runtime's own boto3 (1.42.97 on 2026-10-30) predates managed knowledge bases,
# so the function gets the SDK uv.lock pins, the one the tests' fakes validate against.
SDK = [f"{package}=={version(package)}" for package in ("boto3", "botocore")]


def invoke_model(stack: cdk.Stack, config: Config) -> iam.PolicyStatement:
    """Converse through the inference profile: the profile, and the model in each of its Regions."""
    profile = stack.format_arn(
        service="bedrock", resource="inference-profile", resource_name=config.model_id
    )
    model = config.model_id.split(".", 1)[1]  # the profile ID without its geography prefix
    models = [f"arn:aws:bedrock:{region}::foundation-model/{model}" for region in PROFILE_REGIONS]
    return iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[profile, *models])


@jsii.implements(cdk.ILocalBundling)
class UvInstall:
    """Build the SDK layer with uv on this machine; Docker is only the fallback."""

    def try_bundle(self, output_dir: str, *args: Any, **kwargs: Any) -> bool:
        target = Path(output_dir) / "python"
        platform = ["--python-version", "3.12", "--python-platform", "aarch64-manylinux2014"]
        command = ["uv", "pip", "install", "--target", str(target), *platform, *SDK]
        subprocess.run([*command, "--only-binary", ":all:", "--quiet"], check=True)
        return True


class DemoStack(cdk.Stack):
    def __init__(self, scope: Construct, config: Config, **kwargs: object) -> None:
        super().__init__(scope, "kb-demo", **kwargs)
        knowledge_base, data_source = self.knowledge_base(config)
        self.api(config, knowledge_base)
        self.ingest_workflow(config, knowledge_base, data_source)

    def knowledge_base(
        self, config: Config
    ) -> tuple[bedrock.CfnKnowledgeBase, bedrock.CfnDataSource]:
        """The corpus bucket and a MANAGED knowledge base reading it through the S3 connector."""
        kb, ds = bedrock.CfnKnowledgeBase, bedrock.CfnDataSource
        bucket = s3.Bucket(
            self,
            "Corpus",
            bucket_name=config.bucket_name(self.account),
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,  # so cdk destroy can delete it with the corpus inside
        )
        # The auto-delete handler would otherwise make its own log group, which CloudFormation
        # doesn't own, so cdk destroy left it behind (2026-10-30). Give it one the stack owns.
        auto_delete_logs = logs.LogGroup(
            self,
            "AutoDeleteLogs",
            log_group_name="/aws/lambda/kb-demo-auto-delete-objects",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        provider = self.node.find_child("Custom::S3AutoDeleteObjectsCustomResourceProvider")
        handler = provider.node.find_child("Handler")
        handler.add_property_override("LoggingConfig.LogGroup", auto_delete_logs.log_group_name)
        # Inline, so the policy exists before the knowledge base that depends on the role.
        read_corpus = iam.PolicyDocument(
            statements=[
                iam.PolicyStatement(actions=["s3:ListBucket"], resources=[bucket.bucket_arn]),
                iam.PolicyStatement(
                    actions=["s3:GetObject"], resources=[bucket.arn_for_objects("*")]
                ),
            ]
        )
        role = iam.Role(
            self,
            "KnowledgeBaseRole",
            role_name=config.role_name,
            description="Lets the kb-demo knowledge base read its corpus bucket",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            inline_policies={"kb-demo-s3-read": read_corpus},
        )
        knowledge_base = kb(
            self,
            "KnowledgeBase",
            name=config.knowledge_base_name,
            description="kb-demo: a fictional field-services handbook",
            role_arn=role.role_arn,
            knowledge_base_configuration=kb.KnowledgeBaseConfigurationProperty(
                type="MANAGED",
                managed_knowledge_base_configuration=kb.ManagedKnowledgeBaseConfigurationProperty(
                    embedding_model_type="MANAGED"
                ),
            ),
        )
        # The connector parameters setup sent through boto3, which worked on this account.
        connector = {
            "type": "S3",
            "version": "1",
            "connectionConfiguration": {
                "bucketName": bucket.bucket_name,
                "bucketOwnerAccountId": self.account,
            },
            "deletionProtectionConfiguration": {"enableDeletionProtection": False},
        }
        data_source = ds(
            self,
            "DataSource",
            knowledge_base_id=knowledge_base.attr_knowledge_base_id,
            name=config.data_source_name,
            description="kb-demo corpus bucket",
            data_deletion_policy="DELETE",
            data_source_configuration=ds.DataSourceConfigurationProperty(
                type="MANAGED_KNOWLEDGE_BASE_CONNECTOR",
                managed_knowledge_base_connector_configuration=(
                    ds.ManagedKnowledgeBaseConnectorConfigurationProperty(
                        connector_parameters=connector
                    )
                ),
            ),
        )
        cdk.CfnOutput(self, "BucketName", value=bucket.bucket_name)
        cdk.CfnOutput(self, "KnowledgeBaseId", value=knowledge_base.attr_knowledge_base_id)
        cdk.CfnOutput(self, "DataSourceId", value=data_source.attr_data_source_id)
        return knowledge_base, data_source

    def api(self, config: Config, knowledge_base: bedrock.CfnKnowledgeBase) -> None:
        """POST /ask: IAM-authorised and throttled, answered by a Lambda wrapping the Searcher."""

        log_group = logs.LogGroup(
            self,
            "Logs",
            log_group_name=f"/aws/lambda/{FUNCTION_NAME}",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        # Least privilege: this knowledge base, this model, this log group, nothing else.
        role = iam.Role(
            self,
            "Role",
            role_name=f"{FUNCTION_NAME}-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        log_group.grant_write(role)
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"], resources=[knowledge_base.attr_knowledge_base_arn]
            )
        )
        role.add_to_policy(invoke_model(self, config))

        sdk = lambda_.LayerVersion(
            self,
            "Sdk",
            description=" ".join(SDK),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_12],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            code=lambda_.Code.from_asset(
                str(Path(__file__).parent),
                asset_hash=" ".join(SDK),  # rebuilt only when uv.lock moves the SDK
                asset_hash_type=cdk.AssetHashType.CUSTOM,
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_12.bundling_image,
                    command=["pip", "install", "--target", "/asset-output/python", *SDK],
                    platform="linux/arm64",
                    local=UvInstall(),
                ),
            ),
        )
        function = lambda_.Function(
            self,
            "Ask",
            function_name=FUNCTION_NAME,
            runtime=lambda_.Runtime.PYTHON_3_12,
            architecture=lambda_.Architecture.ARM_64,
            handler="kb_demo.handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(REPO_ROOT / "src"), exclude=["**/__pycache__", "kb_demo/templates"]
            ),
            memory_size=512,
            timeout=cdk.Duration.seconds(29),  # inside the HTTP API's 30 s integration limit
            layers=[sdk],
            role=role,
            log_group=log_group,
            environment={
                "KB_DEMO_KNOWLEDGE_BASE_ID": knowledge_base.attr_knowledge_base_id,
                "KB_DEMO_MODEL_ID": config.model_id,
            },
        )

        # No reserved concurrency: the account's limit is 10, all of which must stay unreserved.
        # Stage throttling is what caps the spend instead.
        api = apigw.HttpApi(self, "Api", api_name="kb-demo", create_default_stage=False)
        stage = apigw.HttpStage(
            self,
            "Stage",
            http_api=api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=2, burst_limit=5),
        )
        api.add_routes(
            path="/ask",
            methods=[apigw.HttpMethod.POST],
            integration=HttpLambdaIntegration("AskIntegration", function),
            authorizer=HttpIamAuthorizer(),
        )

        cdk.CfnOutput(self, "ApiUrl", value=stage.url)
        cdk.CfnOutput(self, "FunctionName", value=function.function_name)

    def ingest_workflow(
        self,
        config: Config,
        knowledge_base: bedrock.CfnKnowledgeBase,
        data_source: bedrock.CfnDataSource,
    ) -> None:
        """Ingest the corpus and check every file was indexed, by SDK integrations alone."""
        role = iam.Role(
            self,
            "IngestRole",
            role_name=f"{config.workflow_name}-role",
            assumed_by=iam.ServicePrincipal(
                "states.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:StartIngestionJob",
                    "bedrock:GetIngestionJob",
                    "bedrock:ListKnowledgeBaseDocuments",
                ],
                resources=[knowledge_base.attr_knowledge_base_arn],
            )
        )
        workflow = sfn.StateMachine(
            self,
            "Ingest",
            state_machine_name=config.workflow_name,
            state_machine_type=sfn.StateMachineType.STANDARD,
            definition_body=sfn.DefinitionBody.from_string(json.dumps(DEFINITION)),
            definition_substitutions={
                "KnowledgeBaseId": knowledge_base.attr_knowledge_base_id,
                "DataSourceId": data_source.attr_data_source_id,
            },
            role=role,
        )
        cdk.CfnOutput(self, "IngestWorkflowArn", value=workflow.state_machine_arn)


class CiStack(cdk.Stack):
    """GitHub Actions' way in: an OIDC provider and two roles, for this repo's v* tags only.

    Deployed by hand, once, and kept apart from the demo stack, so cdk destroy of the demo never
    locks CI out. No AWS keys are stored in GitHub.
    """

    def __init__(self, scope: Construct, config: Config, **kwargs: object) -> None:
        super().__init__(scope, "kb-demo-ci", **kwargs)
        issuer = "token.actions.githubusercontent.com"
        provider = iam.OidcProviderNative(
            self, "GitHub", url=f"https://{issuer}", client_ids=["sts.amazonaws.com"]
        )
        conditions = {
            "StringEquals": {f"{issuer}:aud": "sts.amazonaws.com"},
            "StringLike": {f"{issuer}:sub": f"{GITHUB_SUBJECT}:ref:refs/tags/v*"},
        }

        def github() -> iam.WebIdentityPrincipal:
            return iam.WebIdentityPrincipal(provider.oidc_provider_arn, conditions)

        # The release gate asks the live knowledge base the sample questions with the new code.
        evaluate = iam.Role(
            self,
            "Evaluate",
            role_name="kb-demo-ci-evaluate",
            description="GitHub Actions release gate: ask the kb-demo knowledge base",
            assumed_by=github(),
        )
        evaluate.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:ListKnowledgeBases"], resources=["*"])
        )
        # The knowledge base's ID changes with every rebuild, so match it by its tag instead.
        knowledge_bases = self.format_arn(
            service="bedrock", resource="knowledge-base", resource_name="*"
        )
        tagged = {f"aws:ResourceTag/{key}": value for key, value in TAGS.items()}
        evaluate.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[knowledge_bases],
                conditions={"StringEquals": tagged},
            )
        )
        evaluate.add_to_policy(invoke_model(self, config))

        # Deploying goes through the CDK bootstrap roles, which do the actual work.
        deploy = iam.Role(
            self,
            "Deploy",
            role_name="kb-demo-ci-deploy",
            description="GitHub Actions deploy: may only assume the CDK bootstrap roles",
            assumed_by=github(),
        )
        qualifier = cdk.DefaultStackSynthesizer.DEFAULT_QUALIFIER
        bootstrap = self.format_arn(
            service="iam",
            region="",
            resource="role",
            resource_name=f"cdk-{qualifier}-*-{self.account}-{self.region}",
        )
        deploy.add_to_policy(iam.PolicyStatement(actions=["sts:AssumeRole"], resources=[bootstrap]))


def main() -> None:
    app = cdk.App()
    config = Config.from_env()
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=config.region)
    DemoStack(app, config, env=env)
    CiStack(app, config, env=env)
    for key, value in TAGS.items():
        cdk.Tags.of(app).add(key, value)
    app.synth()


if __name__ == "__main__":
    main()
