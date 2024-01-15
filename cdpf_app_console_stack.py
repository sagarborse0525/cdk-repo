import os
from urllib.parse import urlparse
from pkg_resources import Distribution
from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_iam as iam,
    aws_lambda,
    aws_certificatemanager as acm
)

from cdpf_auth_cdk import common

class CatAuthAppConsoleStack(cdk.Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        stage = self.node.try_get_context('stage')
        stage_context = self.node.try_get_context(stage)

        # 画面を配置するS3/公開用CloudFrontを作成する
        self.create_cdpf_app_console(stage_context, stage)

    def create_cdpf_app_console(self, stage_context,  stage: str):
        frontend_bucket_name = common.get_resource_name(self, stage, 'cdpf-auth-console-s3-bucket')
        frontend_bucket = s3.Bucket(self,'CatFrontendS3Bucket', bucket_name=frontend_bucket_name, encryption=s3.BucketEncryption.S3_MANAGED, versioned=True)

        # OAIを設定する
        oai = cloudfront.OriginAccessIdentity(self, 'FrontendOAI')
        frontend_bucket_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW,
            actions=['s3:GetObject'],
            principals=[
                iam.CanonicalUserPrincipal(oai.cloud_front_origin_access_identity_s3_canonical_user_id)
            ],
            resources=[frontend_bucket.bucket_arn + '/*']
        )
        frontend_bucket.add_to_resource_policy(frontend_bucket_policy)

        s3_origin = origins.S3Origin(
            frontend_bucket,
            origin_access_identity=oai,
            origin_path='/AuthPlatformConsoleClient'
        )

        default_behavior_option=cloudfront.BehaviorOptions(
            origin=s3_origin,
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            origin_request_policy=cloudfront.OriginRequestPolicy.CORS_S3_ORIGIN
        )

        error_responses=[
            cloudfront.ErrorResponse(http_status=403, response_http_status=200, response_page_path='/index.html', ttl=cdk.Duration.millis(0)),
            cloudfront.ErrorResponse(http_status=404, response_http_status=200, response_page_path='/index.html', ttl=cdk.Duration.millis(0))
        ]

        custom_domain_name = stage_context['cdpf-auth-console-custom-domain']
        custom_certificate = None

        # WAFのIDを取得して、空文字列の場合にNoneに変換する
        web_acl_id = stage_context['cdpf-auth-console-waf-id']
        if not web_acl_id:
            web_acl_id = None

        if custom_domain_name and len(custom_domain_name) >0:
            # カスタムドメインに対応したACMの証明書を取得する
            certificate_arn = stage_context['cdpf-auth-console-acm-arn']
            custom_certificate = acm.Certificate.from_certificate_arn(self, 'CustomDomainCertificate',
                certificate_arn
            )

            frontend = cloudfront.Distribution(
                self,
                'DistributionBoardCloudFront',
                default_behavior=default_behavior_option,
                error_responses=error_responses,
                default_root_object='index.html',
                comment=f'CAT認証認可基盤 {stage.upper()}環境',
                certificate=custom_certificate,
                domain_names=[custom_domain_name],
                web_acl_id=web_acl_id
            )
        else:
            # カスタムドメイン名なし
            frontend = cloudfront.Distribution(
                self,
                'DistributionBoardCloudFront',
                default_behavior=default_behavior_option,
                error_responses=error_responses,
                default_root_object='index.html',
                comment=f'CAT認証認可基盤 {stage.upper()}環境',
                web_acl_id=web_acl_id
            )
