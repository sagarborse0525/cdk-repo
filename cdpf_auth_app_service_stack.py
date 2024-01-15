import os
from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as sm,
    aws_apigateway as apigw
)

from cdpf_auth_cdk import common


CONTAINER_CPU = os.getenv('CONTAINER_CPU', 256)
CONTAINER_MEMORY = os.getenv('CONTAINER_MEMORY', 512)

class CatAuthAppServiceStack(cdk.Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        stage = self.node.try_get_context('stage')
        stage_context = self.node.try_get_context(stage)

        # 既存のVPC・Fargate情報を取得する
        vpc = ec2.Vpc.from_lookup(self, 'Vpc', vpc_id=stage_context['vpc-id'])
        ecs_cluster_name = common.get_resource_name(self, stage, 'ecs-cluster-name')
        cluster = ecs.Cluster.from_cluster_attributes(
            self,
            ecs_cluster_name,
            cluster_name=ecs_cluster_name,
            vpc=vpc,
            security_groups=[]
        )

        # CDPF-Auth-serverのアプリコンテナを動かすFargate/ALBのスタックを作成する
        self.create_cdpf_auth_server_main_service(cluster, stage_context, stage)


    def get_registry_name(self, repository: str) -> str:
        return f'{cdk.Stack.of(self).account}.dkr.ecr.us-west-2.amazonaws.com/{repository}'

    def create_cdpf_auth_server_main_service(self, cluster: ecs.Cluster, stage_context,  env_name: str):
        """
        CDPF-Auth-serverのコンテナを動かすFargateサービスとNLBを作成する
        """

        # ECRからイメージを取得する
        ##repo_name = self.get_registry_name(common.get_resource_name(self, env_name, 'cdpf-auth-server-repository'))
        #repo_name = common.get_resource_name(self, env_name, 'cdpf-auth-server-repository')
        #repository = ecr.Repository.from_repository_name(self, 'Repository', repo_name)
        image_tag = stage_context['cdpf-fargate-image-tag']
        repo_arn = stage_context['cdpf-ecr-arn']
        repository = ecr.Repository.from_repository_arn(self, 'Repository', repo_arn)
        image = ecs.ContainerImage.from_ecr_repository(repository, image_tag)

        # ロールを取得する
        task_execution_role_name = common.get_resource_name(self, env_name, 'cdpf-auth-ecs-task-execution-role')
        task_execution_role_arn = f'arn:aws:iam::{cdk.Stack.of(self).account}:role/{task_execution_role_name}'
        execution_role = iam.Role.from_role_arn(
            self,
            task_execution_role_name,
            task_execution_role_arn, mutable=False
        )

        service_task_role_name = common.get_resource_name(self, env_name, 'cdpf-auth-ecs-service-task-role')
        service_task_role_arn = f'arn:aws:iam::{cdk.Stack.of(self).account}:role/{service_task_role_name}'
        service_task_role = iam.Role.from_role_arn(
            self,
            service_task_role_name,
            service_task_role_arn, mutable=False
        )

        # ロールを作成する
        ## execution_role = iam.Role(self, 'EcsTaskExecutionRole',
        ##     assumed_by=iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
        ##     role_name=common.get_resource_name(self, env_name, 'cdpf-auth-ecs-task-execution-role'),
        ##     managed_policies=[
        ##         iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AmazonECSTaskExecutionRolePolicy')
        ##     ])
        ## service_task_role = iam.Role(self, 'EcsServiceTaskRole',
        ##     assumed_by=iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
        ##     role_name=common.get_resource_name(self, env_name, 'cdpf-auth-ecs-service-task-role')
        ##     )

        api_port = self.node.try_get_context('api-port')

        # タスク定義
        task_definition = ecs.FargateTaskDefinition(
            self,
            common.get_resource_name(self, env_name, 'cdpf-auth-ecs-task-definition'),
            cpu=CONTAINER_CPU,
            memory_limit_mib=CONTAINER_MEMORY,
            execution_role=execution_role,
            task_role=service_task_role
        )
        #task_definition = ecs.FargateTaskDefinition.from_fargate_task_definition_arn(
        #    self,
        #    'FargateTaskDef',
        #    stage_context['cdpf-ecs-task-definition-arn']
        #)

        port_mapping = ecs.PortMapping(
            container_port=api_port,
            host_port=api_port,
            protocol=ecs.Protocol.TCP
        )

        # SSM
        db_secrets = sm.Secret.from_secret_name_v2(self, 'Secret-rds', stage_context['cdpf-rds-secrets-name'])
        azure_secrets = sm.Secret.from_secret_name_v2(self, 'Secret-azure', stage_context['cdpf-azure-secrets-name'])

        # タスクにコンテナを追加し、ポートマッピングを行う

        cdpf_auth_server = common.get_resource_name(self, env_name, 'cdpf-auth-server')
        container_name = common.get_resource_name(self, env_name, 'cdpf-auth-ecs-task-definition')
        container_envs = stage_context['cdpf-fargate-env']
        container_secrets = {
            'DB_USERNAME': ecs.Secret.from_secrets_manager(db_secrets, 'username'),
            'DB_PASSWORD': ecs.Secret.from_secrets_manager(db_secrets, 'password'),
            'AZURE_AD_TENANT': ecs.Secret.from_secrets_manager(azure_secrets, 'AZURE_AD_TENANT'),
            'AZURE_AD_CLIENT_ID': ecs.Secret.from_secrets_manager(azure_secrets, 'AZURE_AD_CLIENT_ID'),
            'AZURE_AD_CLIENT_SECRET': ecs.Secret.from_secrets_manager(azure_secrets, 'AZURE_AD_CLIENT_SECRET')
        }
        container_envs['PROFILE'] = env_name

        fargate_container = task_definition.add_container(
            container_name,
            image=image,
            environment=container_envs,
            secrets=container_secrets,
            logging=ecs.AwsLogDriver(
                stream_prefix=container_name,
                log_retention=logs.RetentionDays.ONE_MONTH
            )
        )
        fargate_container.add_port_mappings(port_mapping)

        # Fargateを配置するSubnetを取得する
        subnet_ids = stage_context['pvt-subnet-ids']
        subnet_filter = ec2.SubnetFilter.by_ids(subnet_ids)
        subnet_selection = ec2.SubnetSelection(subnet_filters=[subnet_filter])

        # ecs_patterns.NetworkLoadBalancedFargateServiceだと、
        # Serviceに設定されるセキュリティグループが新規作成されるため、
        # ServiceとNLBを別に作成する
        nlb = elbv2.NetworkLoadBalancer(self, "nlb",
            vpc=cluster.vpc,
            vpc_subnets=subnet_selection,
            load_balancer_name=common.get_resource_name(self, env_name, 'cdpf-auth-lb-name')
        )

        fargate_sg_id = stage_context['cdpf-fargate-sg']
        fargate_sg = ec2.SecurityGroup.from_security_group_id(self, "SG", fargate_sg_id)

        # 初期構築時は、タスクの参照先イメージは事前に作成されないため、サービス数を0で起動する
        # 既にECRが存在している場合は、サービス数を1で起動する
        service = ecs.FargateService(self, "Service",
            cluster=cluster,
            task_definition=task_definition,
            service_name=cdpf_auth_server,
            security_groups=[fargate_sg],
            platform_version=ecs.FargatePlatformVersion.VERSION1_4,
            desired_count=1
        )

        test_service = ecs.FargateService(self, "Service",
            cluster=cluster,
            task_definition=task_definition,
            service_name=cdpf_auth_server,
            security_groups=[fargate_sg],
            platform_version=ecs.FargatePlatformVersion.VERSION1_4,
            desired_count=1
        )

        # NLBからコンテナへのポートマッピングを追加する
        listener = nlb.add_listener("listener", port=80)
        listener.add_targets("ECS",
            port=api_port,
            targets=[
                service.load_balancer_target(
                    container_name=fargate_container.container_name,
                    container_port=fargate_container.container_port
                )
            ]
        )
        # Added by ops
        listener_8080 = nlb.add_listener("listener", port=8080)
        listener.add_targets("ECS",
            port=api_port,
            targets=[
                service.load_balancer_target(
                    container_name=fargate_container.container_name,
                    container_port=fargate_container.container_port
                )
            ]
        )

        acm_arn = stage_context['cdpf-auth-nlb-acm-arn']

        https_listener = nlb.add_listener("https_listener",
                                            port=443,
                                            certificates=[
                                                elbv2.ListenerCertificate.from_arn(acm_arn)
                                            ]
                                          )
        https_listener.add_targets("ECS_tls",
            port=api_port,
            targets=[
                service.load_balancer_target(
                    container_name=fargate_container.container_name,
                    container_port=fargate_container.container_port
                )
            ]
        )
