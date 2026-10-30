"""CDK app: POST /ask on an HTTP API that requires IAM authorisation, answered by a Lambda.

The knowledge base is still made by `kb-demo setup`, so pass its ID as context:
    cdk deploy -c knowledge_base_id=<id from kb-demo status>
"""

import os
import subprocess
from importlib.metadata import version
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import jsii
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk.aws_apigatewayv2_authorizers import HttpIamAuthorizer
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration
from constructs import Construct

from kb_demo.config import REPO_ROOT, TAGS, Config

# The Regions the au. inference profile routes to (get-inference-profile, 2026-10-30). Converse
# through a profile needs InvokeModel on the profile and on the model in each of them.
PROFILE_REGIONS = ("ap-southeast-2", "ap-southeast-4")
FUNCTION_NAME = "kb-demo-ask"
# The python3.12 runtime's own boto3 (1.42.97 on 2026-10-30) predates managed knowledge bases,
# so the function gets the SDK uv.lock pins, the one the tests' fakes validate against.
SDK = [f"{package}=={version(package)}" for package in ("boto3", "botocore")]


@jsii.implements(cdk.ILocalBundling)
class UvInstall:
    """Build the SDK layer with uv on this machine; Docker is only the fallback."""

    def try_bundle(self, output_dir: str, *args: Any, **kwargs: Any) -> bool:
        target = Path(output_dir) / "python"
        platform = ["--python-version", "3.12", "--python-platform", "aarch64-manylinux2014"]
        command = ["uv", "pip", "install", "--target", str(target), *platform, *SDK]
        subprocess.run([*command, "--only-binary", ":all:", "--quiet"], check=True)
        return True


class ApiStack(cdk.Stack):
    def __init__(
        self, scope: Construct, config: Config, knowledge_base_id: str, **kwargs: object
    ) -> None:
        super().__init__(scope, "kb-demo", **kwargs)
        model = config.model_id.split(".", 1)[1]  # the profile ID without its geography prefix

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
        knowledge_base = self.format_arn(
            service="bedrock", resource="knowledge-base", resource_name=knowledge_base_id
        )
        profile = self.format_arn(
            service="bedrock", resource="inference-profile", resource_name=config.model_id
        )
        models = [
            f"arn:aws:bedrock:{region}::foundation-model/{model}" for region in PROFILE_REGIONS
        ]
        role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:Retrieve"], resources=[knowledge_base])
        )
        role.add_to_policy(
            iam.PolicyStatement(actions=["bedrock:InvokeModel"], resources=[profile, *models])
        )

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
                "KB_DEMO_KNOWLEDGE_BASE_ID": knowledge_base_id,
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


def main() -> None:
    app = cdk.App()
    knowledge_base_id = app.node.try_get_context("knowledge_base_id")
    if not knowledge_base_id:
        raise SystemExit("Pass -c knowledge_base_id=<id>; `uv run kb-demo status` shows it.")
    config = Config.from_env()
    env = cdk.Environment(account=os.environ.get("CDK_DEFAULT_ACCOUNT"), region=config.region)
    ApiStack(app, config, knowledge_base_id, env=env)
    for key, value in TAGS.items():
        cdk.Tags.of(app).add(key, value)
    app.synth()


if __name__ == "__main__":
    main()
