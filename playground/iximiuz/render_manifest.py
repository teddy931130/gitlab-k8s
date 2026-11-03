#!/usr/bin/env python3
"""Render the self-contained iximiuz playground manifest for the GitLab lab.

The manifest reproduces the official k8s-omni kubeadm cluster (containerd and
Flannel only), embeds the lab files from this repository as startup files, and
deploys GitLab with the lab Makefile targets in init tasks. Publishing it with
`labctl playground update` replaces the whole playground definition.
"""

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_NAME = "gitlab-k8s-lab-1781ac3c"
DEFAULT_OUTPUT = ROOT / "playground/iximiuz/manifest.yaml"

LAB_DIR = "/opt/gitlab-k8s"
HELM_VERSION = "v4.3.1"
FLANNEL_VERSION = "v0.28.10"
WEB_MACHINE = "cplane-01"
WEB_NODE_PORT = int(
    re.search(r"nodePort:\s*(\d+)", (ROOT / "playground/iximiuz/gitlab-web.yaml").read_text()).group(1)
)

# Repository files copied to LAB_DIR on the dev machine.
LAB_FILES = [
    "Makefile",
    "helm/chart-version",
    "helm/runner-chart-version",
    "helm/values.yaml",
    "helm/values.lab.yaml",
    "helm/values.runner.lab.yaml",
    "helm/values.production.example.yaml",
    "k8s/lab/bootstrap-runner.py",
    "k8s/lab/configure-object-storage.py",
    "k8s/lab/dependencies.yaml",
    "k8s/lab/object-storage.yaml",
    "k8s/lab/object-storage-buckets.yaml",
    "playground/iximiuz/gitlab-web.yaml",
]

# k8s-omni machines. Non-flexbox bases must keep their machine names, drive
# images, and addresses. The resources already use a paid plan's per-playground
# budget (10 vCPU, 16 GiB).
MACHINES = [
    ("dev-machine", "k8s-omni-dev-machine", "172.16.0.5", 2),
    ("cplane-01", "k8s-omni-cplane", "172.16.0.2", 4),
    ("node-01", "k8s-omni-node", "172.16.0.3", 2),
    ("node-02", "k8s-omni-node", "172.16.0.4", 2),
]
WORKERS = [("node1", "node-01"), ("node2", "node-02")]

WELCOME = f"""GitLab CE on Kubernetes lab

  Web UI:          click the GitLab tab (opens a new browser tab)
  Sign in:         root, password in ~/gitlab-root-password
  Lab files:       {LAB_DIR}
  Deploy status:   make -C {LAB_DIR} status
"""

# --- k8s-omni cluster tasks, copied from the base (containerd + Flannel). ---

CONTAINERD_INSTALL = """mv /opt/iximiuz-labs/containerd/containerd.service /etc/systemd/system/containerd.service

chmod +x /opt/iximiuz-labs/containerd/bin/*
mv /opt/iximiuz-labs/containerd/bin/* /usr/local/bin

mkdir -p /etc/containerd
cat <<EOF > /etc/containerd/config.toml
version = 2

[plugins."io.containerd.grpc.v1.cri"]
  [plugins."io.containerd.grpc.v1.cri".containerd]
    snapshotter = "overlayfs"
    default_runtime_name = "runc"
  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc]
    runtime_type = "io.containerd.runc.v2"
  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runc.options]
    SystemdCgroup = true
[plugins."io.containerd.grpc.v1.cri".cni]
  bin_dir = "/opt/cni/bin"
  conf_dir = "/etc/cni/net.d"
EOF

systemctl daemon-reload
systemctl enable --now containerd
"""

LOAD_IMAGES = """for i in $(ls /opt/iximiuz-labs/images); do
  ctr -n k8s.io images import "/opt/iximiuz-labs/images/$i"
done

rm -rf /opt/iximiuz-labs/images
"""

COPY_KUBECONFIG = """mkdir -p /root/.kube
scp -o StrictHostKeyChecking=no cplane-01:/etc/kubernetes/admin.conf /root/.kube/config

mkdir -p /home/laborant/.kube
cp /root/.kube/config /home/laborant/.kube/config
chown -R laborant:laborant /home/laborant/.kube
"""


def join_node(machine):
    return f"""if kubectl get nodes/{machine}; then
  exit 0
fi

eval "$(kubeadm token create --print-join-command)"
sleep 1

if ! kubectl get nodes/{machine}; then
  exit 1
fi
"""


# --- GitLab lab tasks on the dev machine. ---

PRELUDE = f"""set -Eeuo pipefail
export HOME=/root KUBECONFIG=/root/.kube/config DEBIAN_FRONTEND=noninteractive
cd {LAB_DIR}
"""

INSTALL_TOOLS = PRELUDE + f"""chown -R laborant:laborant {LAB_DIR}

apt-get update
apt-get install -y --no-install-recommends ca-certificates curl make openssl python3 tar

HELM_VERSION={HELM_VERSION}
if [ "$(helm version --template '{{{{.Version}}}}' 2>/dev/null || true)" != "$HELM_VERSION" ]; then
  case "$(uname -m)" in
    x86_64) HELM_ARCH=amd64 ;;
    aarch64) HELM_ARCH=arm64 ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
  HELM_ARCHIVE="helm-${{HELM_VERSION}}-linux-${{HELM_ARCH}}.tar.gz"
  HELM_TMP="$(mktemp -d)"
  trap 'rm -rf "$HELM_TMP"' EXIT
  curl -fsSL --retry 5 "https://get.helm.sh/$HELM_ARCHIVE" -o "$HELM_TMP/$HELM_ARCHIVE"
  curl -fsSL --retry 5 "https://get.helm.sh/$HELM_ARCHIVE.sha256sum" -o "$HELM_TMP/$HELM_ARCHIVE.sha256sum"
  (cd "$HELM_TMP" && sha256sum --check "$HELM_ARCHIVE.sha256sum")
  tar -xzf "$HELM_TMP/$HELM_ARCHIVE" -C "$HELM_TMP"
  install "$HELM_TMP/linux-$HELM_ARCH/helm" /usr/local/bin/helm
fi
helm version
"""

DEPENDENCIES = PRELUDE + """kubectl wait --for=condition=Ready nodes --all --timeout=10m
kubectl -n kube-system rollout status deployment/coredns --timeout=10m

kubectl create namespace gitlab --dry-run=client -o yaml | kubectl apply -f -

# Generate each credential once. A retried task must not replace a password
# that PostgreSQL or Gitaly has already been initialized with.
umask 077
create_random_secret() {
  if kubectl -n gitlab get secret "$1" >/dev/null 2>&1; then
    return
  fi
  value_file="$(mktemp)"
  openssl rand -hex 32 > "$value_file"
  kubectl -n gitlab create secret generic "$1" --from-file="$2=$value_file"
  rm -f "$value_file"
}
create_random_secret gitlab-postgresql-password password
create_random_secret gitlab-gitaly-token token

make lab-dependencies
kubectl -n gitlab rollout status deployment/gitlab-postgresql --timeout=10m
kubectl -n gitlab rollout status deployment/gitlab-redis --timeout=10m
"""

BROWSER_ACCESS = PRELUDE + f"""kubectl -n gitlab rollout status deployment/gitlab-webservice-default --timeout=10m
kubectl apply -f playground/iximiuz/gitlab-web.yaml
kubectl -n gitlab rollout status deployment/gitlab-playground-web --timeout=5m
curl -fsS -o /dev/null --retry 60 --retry-delay 5 --retry-all-errors \\
  http://{WEB_MACHINE}:{WEB_NODE_PORT}/users/sign_in

install -o laborant -g laborant -m 600 /dev/null /home/laborant/gitlab-root-password
kubectl -n gitlab get secret gitlab-gitlab-initial-root-password -o jsonpath='{{.data.password}}' |
  base64 -d > /home/laborant/gitlab-root-password

helm status gitlab -n gitlab
kubectl -n gitlab get pods
"""


def task(machine, run, needs=None, timeout=300):
    entry = {"machine": machine, "init": True, "user": "root", "timeout_seconds": timeout}
    if needs:
        entry["needs"] = needs
    entry["run"] = run
    return entry


def init_tasks():
    tasks = {
        "init_cplane_10_containerd_install": task("cplane-01", CONTAINERD_INSTALL),
        "init_cplane_15_containerd_load_images": task(
            "cplane-01", LOAD_IMAGES, ["init_cplane_10_containerd_install"]),
        "init_cplane_20_kubeadm_init": task(
            "cplane-01", "kubeadm init --pod-network-cidr=10.244.0.0/16\n",
            ["init_cplane_15_containerd_load_images"]),
        "init_cplane_30_place_kubeconfig": task("cplane-01", """mkdir /root/.kube
ln -s /etc/kubernetes/admin.conf /root/.kube/config

mkdir /home/laborant/.kube
cp /etc/kubernetes/admin.conf /home/laborant/.kube/config
chown -R laborant:laborant /home/laborant/.kube
""", ["init_cplane_20_kubeadm_init"]),
        "init_cplane_40_taint": task(
            "cplane-01", "kubectl taint node cplane-01 node-role.kubernetes.io/control-plane:NoSchedule-\n",
            ["init_cplane_30_place_kubeconfig"]),
        "init_cplane_50_flannel": task(
            "cplane-01",
            f"kubectl apply -f https://github.com/flannel-io/flannel/releases/download/{FLANNEL_VERSION}/kube-flannel.yml\n",
            ["init_cplane_40_taint"]),
    }
    for prefix, machine in WORKERS:
        tasks[f"init_{prefix}_10_containerd_install"] = task(machine, CONTAINERD_INSTALL)
        tasks[f"init_{prefix}_15_containerd_load_images"] = task(
            machine, LOAD_IMAGES, [f"init_{prefix}_10_containerd_install"])
        tasks[f"init_{prefix}_20_place_kubeconfig"] = task(
            machine, COPY_KUBECONFIG,
            [f"init_{prefix}_15_containerd_load_images", "init_cplane_20_kubeadm_init"])
        tasks[f"init_{prefix}_40_join"] = task(
            machine, join_node(machine), [f"init_{prefix}_20_place_kubeconfig"])

    dev = "dev-machine"
    tasks.update({
        "init_dev_machine_10_place_kubeconfig": task(dev, COPY_KUBECONFIG, ["init_cplane_20_kubeadm_init"]),
        "init_dev_machine_20_install_tools": task(
            dev, INSTALL_TOOLS, ["init_dev_machine_10_place_kubeconfig"], timeout=600),
        "init_dev_machine_30_gitlab_dependencies": task(dev, DEPENDENCIES, [
            "init_dev_machine_20_install_tools",
            "init_cplane_50_flannel",
            "init_node1_40_join",
            "init_node2_40_join",
        ], timeout=1500),
        "init_dev_machine_40_object_storage": task(
            dev, PRELUDE + "make lab-object-storage\n", ["init_dev_machine_30_gitlab_dependencies"], timeout=900),
        "init_dev_machine_50_gitlab": task(
            dev, PRELUDE + "make lab-gitlab\n", ["init_dev_machine_40_object_storage"], timeout=2700),
        "init_dev_machine_55_runner": task(
            dev, PRELUDE + "make lab-runner\n", ["init_dev_machine_50_gitlab"], timeout=1200),
        "init_dev_machine_60_browser_access": task(
            dev, BROWSER_ACCESS, ["init_dev_machine_55_runner"], timeout=900),
    })
    return tasks


def machines():
    result = []
    for name, drive, address, cpus in MACHINES:
        laborant = {"name": "laborant", "default": True}
        if name == "dev-machine":
            laborant["welcome"] = WELCOME
        result.append({
            "name": name,
            "users": [{"name": "root"}, laborant],
            "drives": [{"source": drive, "mount": "/", "size": "30GiB"}],
            "network": {"interfaces": [{"address": f"{address}/24", "network": "local"}]},
            "resources": {"cpuCount": cpus, "ramSize": "4G"},
        })
    return result


def tabs():
    return [
        {"id": "terminal-dev-machine", "kind": "terminal", "name": "dev-machine", "machine": "dev-machine"},
        {"id": "gitlab", "kind": "http-port", "name": "GitLab", "machine": WEB_MACHINE,
         "number": WEB_NODE_PORT, "target": "window"},
        {"id": "ide-dev-machine", "kind": "ide", "name": "IDE", "machine": "dev-machine"},
        {"id": "kexp-dev-machine", "kind": "kexp", "name": "Explorer", "machine": "dev-machine"},
        {"id": "terminal-cplane-01", "kind": "terminal", "name": "cplane-01", "machine": "cplane-01"},
        {"id": "terminal-node-01", "kind": "terminal", "name": "node-01", "machine": "node-01"},
        {"id": "terminal-node-02", "kind": "terminal", "name": "node-02", "machine": "node-02"},
    ]


MARKDOWN = f"""A fresh kubeadm cluster (one control plane, two workers, and a dev machine) that installs GitLab CE with the official Helm chart when it starts. Nothing to run by hand: when the playground opens, GitLab is up.

## Startup

Startup takes about 10 minutes. The init tasks build the cluster, then deploy GitLab in the same stages as `make deploy-lab`:

1. In-cluster PostgreSQL and Redis, with freshly generated credentials, plus the Gateway API CRDs.
2. RustFS object storage and its buckets.
3. The full GitLab release: object storage, Container Registry, Prometheus, and the HTTPS Gateway.
4. A standalone GitLab Runner in the `runner` namespace, with one job at a time.

## Sign in

1. Click the **GitLab** tab. It opens GitLab in a new browser tab.
2. Sign in as `root`. The generated password is in the dev-machine terminal:

   ```sh
   cat ~/gitlab-root-password
   ```

The lab files are in `{LAB_DIR}` on the dev machine, with `kubectl` and `helm` set up. Run `make -C {LAB_DIR} status` to see the release and pods.

## How the GitLab tab connects

The chart serves GitLab over HTTPS at `gitlab.gitlab.local` through Envoy Gateway, with a self-signed certificate. That hostname cannot match the playground's own address, so the tab goes to a small nginx proxy on NodePort `{WEB_NODE_PORT}` that forwards to GitLab Workhorse and keeps redirects on the tab's address. GitLab still uses `https://gitlab.gitlab.local` in clone URLs and emails.

To use the Gateway itself, run `labctl kube-proxy` on your machine, then port-forward the `envoy-gitlab-gitlab-gw-*` service in the `gitlab` namespace to local port 8080 (`kubectl -n gitlab port-forward svc/<name> 8080:443`). Map `gitlab.gitlab.local` to `127.0.0.1` in `/etc/hosts`, and visit `https://gitlab.gitlab.local:8080`.

## Limits

Starting this playground needs a paid iximiuz plan. On the free tier, its machines are scaled down to 5 vCPU and 8 GiB in total, which is too small for GitLab.

This is a disposable evaluation lab. PostgreSQL, Redis, RustFS, and Gitaly use `emptyDir` volumes, so their data is lost when their pods are recreated. The cluster has about 11 GiB of allocatable memory, well below GitLab's sizing guidance. The runner executes one untagged job at a time in its own namespace.
"""


def render(name=DEFAULT_NAME):
    files = [
        {
            "path": f"{LAB_DIR}/{path}",
            "owner": "root",
            "mode": "644",
            "content": (ROOT / path).read_text(),
            "machines": ["dev-machine"],
        }
        for path in LAB_FILES
    ]
    return {
        "kind": "playground",
        "name": name,
        "base": "k8s-omni",
        "title": "GitLab CE on Kubernetes",
        "description": (
            "A multi-node kubeadm cluster that installs GitLab CE with the official Helm chart at startup, "
            "with in-cluster PostgreSQL, Redis, RustFS object storage, Container Registry, and an Envoy HTTPS Gateway."
        ),
        "categories": ["kubernetes", "ci-cd"],
        "markdown": MARKDOWN,
        "playground": {
            "networks": [{"name": "local", "subnet": "172.16.0.0/24"}],
            "machines": machines(),
            "startupFiles": files,
            "tabs": tabs(),
            "initTasks": init_tasks(),
            "accessControl": {"canList": ["anyone"], "canRead": ["anyone"], "canStart": ["anyone"]},
        },
    }


# --- Minimal YAML emitter: block scalars keep embedded files reviewable. ---

def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _block(text, indent):
    if text[:1] in (" ", "\t"):
        raise ValueError("block scalars must not start with whitespace")
    stripped = text.rstrip("\n")
    trailing = len(text) - len(stripped)
    indicator = "|-" if trailing == 0 else "|" if trailing == 1 else "|+"
    pad = " " * indent
    lines = [f"{pad}{line}" if line else "" for line in stripped.split("\n")]
    lines += [""] * max(trailing - 1, 0)
    return indicator, lines


def dump_yaml(value, indent=0):
    pad = " " * indent
    lines = []
    if isinstance(value, dict):
        items = [(f"{key}:", item) for key, item in value.items()]
    elif isinstance(value, list):
        items = [("-", item) for item in value]
    else:
        raise TypeError(type(value))
    for prefix, item in items:
        if isinstance(item, (dict, list)):
            lines.append(f"{pad}{prefix}")
            lines.append(dump_yaml(item, indent + 2))
        elif isinstance(item, str) and "\n" in item:
            indicator, body = _block(item, indent + 2)
            lines.append(f"{pad}{prefix} {indicator}")
            lines.extend(body)
        else:
            lines.append(f"{pad}{prefix} {_scalar(item)}")
    return "\n".join(lines)


def manifest_text(name=DEFAULT_NAME):
    return (
        "# Generated by playground/iximiuz/render_manifest.py; edit the sources instead.\n"
        "# Publish with: bash playground/iximiuz/publish.sh\n"
        + dump_yaml(render(name)) + "\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help=f"registered playground name, or a new name for labctl playground create (default: {DEFAULT_NAME})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.write_text(manifest_text(args.name))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
