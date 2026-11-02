#!/usr/bin/env python3

import base64
import json
import os
import re
import secrets
import subprocess
import sys


NAMESPACE = os.environ.get("NAMESPACE", "gitlab")
ENDPOINT = "http://gitlab-minio.gitlab.svc.cluster.local:9000"


def get_secret(name):
    result = subprocess.run(
        ["kubectl", "get", "secret", name, "--namespace", NAMESPACE, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout).get("data", {})


def decode(data, key):
    try:
        return base64.b64decode(data[key], validate=True).decode()
    except (KeyError, ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"Secret data is missing or invalid: {key}") from error


def encoded(value):
    return base64.b64encode(value.encode()).decode()


def secret(name, key, value):
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "type": "Opaque",
        "data": {key: encoded(value)},
    }


def apply(documents):
    payload = "\n---\n".join(json.dumps(document) for document in documents)
    subprocess.run(
        ["kubectl", "apply", "--namespace", NAMESPACE, "-f", "-"],
        input=payload,
        check=True,
        text=True,
    )


def object_storage_auth():
    """Return the RustFS credentials, generating them on the first run.

    The GitLab chart removed its bundled MinIO, so it no longer generates these.
    """
    try:
        return get_secret("gitlab-minio-auth")
    except subprocess.CalledProcessError:
        pass
    auth = secret("gitlab-minio-auth", "root-user", secrets.token_hex(10))
    auth["data"]["root-password"] = encoded(secrets.token_hex(32))
    apply([auth])
    return auth["data"]


try:
    minio_auth = object_storage_auth()
    access_key = decode(minio_auth, "root-user")
    secret_key = decode(minio_auth, "root-password")

    try:
        existing_connection = decode(
            get_secret("gitlab-object-storage"), "connection"
        )
    except subprocess.CalledProcessError:
        existing_connection = ""

    connection_matches = all(
        re.search(rf"^{key}:\s*{re.escape(value)}\s*$", existing_connection, re.M)
        for key, value in (
            ("aws_access_key_id", access_key),
            ("aws_secret_access_key", secret_key),
            ("endpoint", ENDPOINT),
        )
    )
    if existing_connection and not connection_matches:
        raise ValueError(
            "gitlab-object-storage does not match gitlab-minio-auth; "
            "refusing to overwrite an existing storage configuration"
        )

    documents = []
    if not existing_connection:
        connection = "\n".join(
            (
                "provider: AWS",
                "region: us-east-1",
                f"aws_access_key_id: {json.dumps(access_key)}",
                f"aws_secret_access_key: {json.dumps(secret_key)}",
                f"endpoint: {ENDPOINT}",
                "path_style: true",
            )
        )
        documents.append(secret("gitlab-object-storage", "connection", connection))

    registry_config = "\n".join(
        (
            "s3:",
            f"  accesskey: {json.dumps(access_key)}",
            f"  secretkey: {json.dumps(secret_key)}",
            "  bucket: gitlab-registry",
            "  region: us-east-1",
            f"  regionendpoint: {ENDPOINT}",
            "  secure: false",
            "  v4auth: true",
        )
    )
    documents.append(
        secret("gitlab-registry-storage", "config", registry_config)
    )

    apply(documents)
except (subprocess.CalledProcessError, ValueError) as error:
    print(f"Unable to configure lab object storage: {error}", file=sys.stderr)
    sys.exit(1)
