from aws_cdk import (
    # Duration,
    Stack,
    # aws_sqs as sqs,
)
from constructs import Construct

class CdkRepoStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        s = self.node.get

        # The code that defines your stack goes here

        # example resource
        # queue = sqs.Queue(
        #     self, "CdkRepoQueue",
        #     visibility_timeout=Duration.seconds(300),
        # )
