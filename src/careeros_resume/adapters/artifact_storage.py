from contextlib import closing
from typing import TYPE_CHECKING
from uuid import UUID

from botocore.exceptions import BotoCoreError, ClientError

from careeros_resume.application.drafts import SourceUnavailable
from careeros_resume.application.exports import MEDIA_TYPES
from careeros_resume.application.versions import NotFound, RegistrationConflict
from careeros_resume.domain.version import Artifact

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def _key(scope: UUID, artifact: Artifact) -> str:
    return f"{scope.hex}/{artifact.object_key.hex}.{artifact.format}"


class S3Artifacts:
    def __init__(self, client: S3Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def write(self, scope: UUID, artifact: Artifact, data: bytes, fingerprint: str) -> None:
        key = _key(scope, artifact)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=MEDIA_TYPES[artifact.format],
                Metadata={"fingerprint": fingerprint},
                IfNoneMatch="*",
            )
            return
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise SourceUnavailable from None
        except BotoCoreError:
            raise SourceUnavailable from None

        try:
            existing = self._client.head_object(Bucket=self._bucket, Key=key)
        except (BotoCoreError, ClientError):
            raise SourceUnavailable from None
        if (
            existing.get("Metadata", {}).get("fingerprint") != fingerprint
            or existing.get("ContentLength") != artifact.byte_size
        ):
            raise RegistrationConflict

    def read(self, scope: UUID, artifact: Artifact) -> bytes:
        try:
            result = self._client.get_object(Bucket=self._bucket, Key=_key(scope, artifact))
            with closing(result["Body"]) as body:
                return body.read(artifact.byte_size + 1)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise NotFound from None
            raise SourceUnavailable from None
        except BotoCoreError:
            raise SourceUnavailable from None
