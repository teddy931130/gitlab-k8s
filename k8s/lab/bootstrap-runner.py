#!/usr/bin/env python3
"""Create an instance runner and its Kubernetes Secret in a fresh lab session."""

import base64
import contextlib
import json
import socket
import subprocess
import time
import urllib.parse
import urllib.request


RUNNER_NAMESPACE = "runner"
RUNNER_SECRET = "lab-runner-auth"
WEB_DEPLOYMENT = "deployment/gitlab-webservice-default"


def kubectl(*args, input_text=None):
    return subprocess.run(
        ["kubectl", *args], input=input_text, text=True, capture_output=True, check=True
    ).stdout


def rails(code):
    return kubectl(
        "-n", "gitlab", "exec", WEB_DEPLOYMENT, "-c", "webservice", "--",
        "/srv/gitlab/bin/rails", "runner", code,
    )


@contextlib.contextmanager
def gitlab_api():
    # The iximiuz dev machine has kubeconfig but is outside cluster DNS and
    # Service routing. Forward Workhorse locally for the one-time API call.
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    forward = subprocess.Popen(
        ["kubectl", "-n", "gitlab", "port-forward", "svc/gitlab-webservice-default", f"{port}:8181"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(60):
            if forward.poll() is not None:
                raise RuntimeError("GitLab API port-forward exited before becoming ready")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(0.5)
        else:
            raise TimeoutError("GitLab API port-forward did not become ready")
        yield f"http://127.0.0.1:{port}/api/v4"
    finally:
        forward.terminate()
        forward.wait(timeout=10)


def main():
    namespace = kubectl("create", "namespace", RUNNER_NAMESPACE, "--dry-run=client", "-o", "yaml")
    kubectl("apply", "-f", "-", input_text=namespace)
    if subprocess.run(
        ["kubectl", "-n", RUNNER_NAMESPACE, "get", "secret", RUNNER_SECRET],
        capture_output=True,
    ).returncode == 0:
        print("Runner authentication Secret already exists; keeping its token")
        return

    # Rails creates a short-lived, narrowly scoped PAT. Only the token and ID
    # are returned to this process; the token is never put in task logs.
    create_pat = '''require "json"
require "securerandom"
user = User.find_by_username("root") or raise "root account missing"
pat = user.personal_access_tokens.create!(name: "iximiuz-runner-bootstrap", scopes: ["create_runner"], expires_at: 1.day.from_now)
raw = SecureRandom.alphanumeric(20)
pat.set_token(raw)
pat.save!
puts JSON.generate({"id" => pat.id, "token" => raw})'''
    with gitlab_api() as api:
        pat = json.loads(rails(create_pat).splitlines()[-1])
        try:
            request = urllib.request.Request(
                api + "/user/runners",
                data=urllib.parse.urlencode({
                    "runner_type": "instance_type",
                    "description": "iximiuz lab runner",
                    "run_untagged": "true",
                }).encode(),
                headers={
                    "PRIVATE-TOKEN": pat["token"],
                    "Host": "gitlab.gitlab.local",
                    "X-Forwarded-Proto": "https",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                runner = json.load(response)
            token = runner["token"]
            if not token.startswith("glrt-"):
                raise RuntimeError("GitLab did not return a runner authentication token")
            secret = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": RUNNER_SECRET, "namespace": RUNNER_NAMESPACE},
                "type": "Opaque",
                "data": {
                    "runner-registration-token": "",
                    "runner-token": base64.b64encode(token.encode()).decode(),
                },
            }
            kubectl("apply", "-f", "-", input_text=json.dumps(secret))
            print(f"Created GitLab instance runner {runner['id']} and its namespace Secret")
        finally:
            revoke = urllib.request.Request(
                api + "/personal_access_tokens/self",
                headers={"PRIVATE-TOKEN": pat["token"]},
                method="DELETE",
            )
            with urllib.request.urlopen(revoke, timeout=60):
                pass


if __name__ == "__main__":
    main()
