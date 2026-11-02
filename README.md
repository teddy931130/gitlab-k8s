# GitLab on Kubernetes

This repository deploys GitLab Self-Managed with GitLab's official Helm chart. Its stable baseline, verified against upstream releases and compatibility documentation on 2026-11-01, is GitLab chart `10.5.4` (GitLab `19.5.3`), PostgreSQL `18.6`, and Redis `7.2.17`. The pinned chart bundles Envoy Gateway `1.9.3`. Keep the chart configuration, version pin, validation pipeline, and operational guidance in version control. Helm renders and manages the GitLab resources; this repository intentionally does not copy or fork the upstream chart.

The configuration is a production-oriented, provider-neutral starting point. It expects external PostgreSQL, Redis or Valkey, S3-compatible object storage, and Gitaly. Those services, the Kubernetes cluster, DNS, and the secret-management system must be provisioned for your environment before installation. No cloud provider, domain, credentials, or Kubernetes context is assumed here.

The checked-in [lab values](./helm/values.lab.yaml) and [lab manifests](./k8s/lab) are only for the disposable playground: PostgreSQL `18.6`, Redis `7.2.17`, and RustFS `1.0.4` run in-cluster, and they and the chart's Gitaly use `emptyDir` storage. RustFS provides S3-compatible object storage; the lab enables Container Registry and bundled Prometheus, and serves the Gateway over HTTPS with the chart-generated self-signed certificate. It is not production-ready: the playground has about 11 GiB of allocatable memory, below GitLab's 30 GB chart recommendation; lab data is lost when its pods are recreated; and the self-signed CA must be trusted by clients. GitLab Runner remains disabled because it requires a runner authentication token and the chart warns that runners do not work properly with self-signed certificates. The cluster also lacks Metrics Server, so HPA CPU targets remain unavailable even with Prometheus installed. GitLab 19.x supports PostgreSQL 17.x–18.x, and PostgreSQL 18.6 is its latest minor release at the time of this baseline. GitLab currently recommends the Redis 7.2 series. Refresh this baseline against the official compatibility docs and upstream chart before upgrades.

## Repository layout

```text
.
├── .github/
│   └── workflows/
│       └── validate-helm.yml
├── .gitignore
├── .gitlab-ci.yml
├── Makefile
├── README.md
├── docs/
│   └── operations.md
├── helm/
│   ├── chart-version
│   ├── values.lab.yaml
│   ├── values.yaml
│   └── values.production.example.yaml
├── k8s/
│   └── lab/
│       ├── configure-object-storage.py
│       ├── dependencies.yaml
│       ├── object-storage-buckets.yaml
│       └── object-storage.yaml
├── playground/
│   └── iximiuz/
│       ├── gitlab-web.yaml
│       ├── manifest.yaml
│       ├── publish.sh
│       └── render_manifest.py
└── tests/
    └── test_playground_manifest.py
```

`helm/values.production.yaml` is the ignored, environment-specific overlay. Keep credentials out of both values files; they reference Kubernetes Secrets by name.

[`playground/iximiuz/`](./playground/iximiuz) holds the complete iximiuz Labs playground manifest that builds a fresh cluster and deploys the lab at startup. See [Iximiuz Labs playground](#iximiuz-labs-playground).

## Before installing

1. Provision a Kubernetes cluster and confirm the exact GitLab/chart release you select works with your Kubernetes distribution and other cluster add-ons. The official chart prerequisites require Helm 4.0 or later and a `kubectl` version within one minor version of the cluster. Consult provider-specific guidance rather than assuming a universal Kubernetes support matrix.
2. Plan cluster capacity using GitLab's sizing and reference-architecture guidance. GitLab's cloud setup guide says the chart is intended for a cluster with at least 8 vCPU and 30 GB RAM; do not treat chart defaults as workload-specific sizing.
3. Provision supported external PostgreSQL, Redis or Valkey, and Gitaly services. PostgreSQL needs the GitLab-required `amcheck`, `btree_gist`, and `pg_trgm` extensions and a database/user with the required privileges. Match PostgreSQL and Redis/Valkey versions to the [requirements for the pinned GitLab release](https://docs.gitlab.com/install/requirements/#postgresql). Gitaly must be reachable from the cluster and use the matching authentication token.
4. Provision durable object storage and create a distinct bucket for each configured data type and for backups. The sample values use S3-compatible storage; backup/restore support should be checked against the selected backend.
5. Ensure the cluster's storage classes are suitable for any chart-managed persistent volumes. GitLab recommends fast SSD storage and a `Retain` reclaim policy for the default StorageClass; Gitaly's StatefulSet PVCs have different retention behavior. Changing storage after installation requires additional migration work.
6. Plan DNS and TLS. The chart-managed Gateway API/Envoy Gateway setup expects the required Gateway API support. Point the GitLab, Registry, and KAS hostnames at the Gateway's external address. The example uses cert-manager and Let's Encrypt, so the domain must be publicly reachable for ACME validation. For private installations, replace that TLS configuration with your certificate workflow.
7. Create the required Kubernetes Secrets in the `gitlab` namespace before deploying. Prefer your organization's external secret manager or workload identity where supported. See [Secrets and credentials](#secrets-and-credentials).

## Configure and deploy

1. Copy `helm/values.production.example.yaml` to `helm/values.production.yaml`.
2. Replace all `example.invalid` values with real hostnames and your ACME registration email. Keep the GitLab base domain aligned with your DNS records. Set PostgreSQL, Redis/Valkey, and Gitaly endpoints to the external services you provisioned. Adjust the Redis scheme and TLS settings if those services require TLS.
3. Create the namespace and all referenced Secrets before deploying. Do not commit the production overlay or secret material.
4. Review the pinned release in [`helm/chart-version`](./helm/chart-version) and its [GitLab version mapping](https://docs.gitlab.com/charts/installation/version_mappings/). Keep the chart version pinned; chart and GitLab version numbers are different.
5. Render the checked-in example configuration:

   ```sh
   make validate
   ```

6. Create the namespace:

   ```sh
   make namespace
   ```

   Then provision the referenced Secrets in that namespace using your secret-management workflow.
7. Install GitLab:

   ```sh
   make deploy
   ```

   This installs the Gateway API and Envoy Gateway CRDs, pre-renders the production values, then runs `helm upgrade --install` with the pinned chart and waits up to 20 minutes for resources to become ready. The `ENVOY_GATEWAY_VERSION` in the Makefile must match the bundled Envoy Gateway dependency of the pinned GitLab chart. It requires a working Kubernetes context and the external services and Secrets to be ready first.

For the disposable playground setup only, provision the `gitlab-postgresql-password` and `gitlab-gitaly-token` Secrets, then run `make deploy-lab`. The target runs three stages, which are also separate targets: `lab-dependencies` installs the Gateway API CRDs, PostgreSQL, and Redis; `lab-object-storage` generates RustFS credentials, starts RustFS, and creates its buckets and the GitLab object-storage Secrets; `lab-gitlab` installs the chart with object storage, Registry, Prometheus, and HTTPS enabled. The PostgreSQL, Redis, RustFS, and Gitaly data directories use `emptyDir`; recreating one of those pods loses its data. This setup is disposable and must not be used for production data. Export and verify a backup before planned PostgreSQL major-version upgrades.

8. Inspect deployment health:

   ```sh
   make status
   kubectl get gateway -n gitlab
   ```

   Configure DNS to the external address reported by the Gateway's Envoy service. Check `helm status gitlab -n gitlab`, pod events, and logs if any component is not ready.

   A successful web request does not by itself mean the Helm operation completed. Confirm the release status is `deployed`; if it is `pending-upgrade`, inspect `helm history gitlab -n gitlab` and resolve the pending release before attempting another upgrade.

The lab Gateway uses a chart-generated self-signed certificate. Retrieve and trust its CA only on clients that need to access this playground:

```sh
kubectl get secret gitlab-wildcard-tls-ca -n gitlab \
  -o jsonpath='{.data.cfssl_ca}' |
  python3 -c 'import base64,sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))' \
  > gitlab.gitlab.local.ca.pem
```

The Gateway Envoy service name has a generated suffix. Find it with:

```sh
kubectl get services -n gitlab -o name | grep 'envoy-gitlab-gitlab-gw'
```

Forward its HTTPS service port (`443`) to local port `8080` (replace the service name below if the generated suffix differs):

```sh
kubectl -n gitlab port-forward svc/envoy-gitlab-gitlab-gw-80260869 8080:443
```

Keep the command running and visit `https://gitlab.gitlab.local:8080`. Map `gitlab.gitlab.local` to `127.0.0.1` in `/etc/hosts`; the browser must trust the lab's self-signed CA or show a certificate warning. This is HTTPS, not HTTP. The lab Runner remains disabled until an instance runner is created in GitLab and its authentication token is securely provisioned; use a trusted certificate for a runner-enabled setup.

The chart's initial root password is generated during installation. Retrieve it securely, sign in, change it, and disable or restrict public sign-up as appropriate:

```sh
kubectl get secret gitlab-gitlab-initial-root-password \
  --namespace gitlab \
  --output jsonpath='{.data.password}' |
  python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.buffer.read()).decode())'
```

## Iximiuz Labs playground

**Playground:** <https://labs.iximiuz.com/playgrounds/gitlab-k8s-lab-1781ac3c>

Click **Start** and the playground builds a fresh kubeadm cluster, then deploys this lab. Nothing has to be run by hand. Startup takes about 10 minutes; two clean test runs took 6.5 and 7.5 minutes. When it finishes, click the **GitLab** tab to open GitLab in a new browser tab and sign in as `root`. The generated password is in `~/gitlab-root-password` on the dev machine.

[`playground/iximiuz/manifest.yaml`](./playground/iximiuz/manifest.yaml) is the complete playground definition. It is generated by [`render_manifest.py`](./playground/iximiuz/render_manifest.py), which:

- Reproduces the official `k8s-omni` cluster: a dev machine, one control plane, and two workers, using containerd and Flannel (pinned). Its machines use the base's VM images and the full resource budget of a paid plan (10 vCPU, 16 GiB).
- Embeds the Makefile, Helm values, and `k8s/lab` manifests as startup files in `/opt/gitlab-k8s` on the dev machine. The playground runs exactly the files in the manifest and does not clone this repository.
- Adds init tasks on the dev machine that install Helm, generate the PostgreSQL and Gitaly secrets once, and run the three `deploy-lab` stages as separate tasks, so startup progress shows which stage is running.
- Applies [`gitlab-web.yaml`](./playground/iximiuz/gitlab-web.yaml) for the **GitLab** tab: an nginx proxy on NodePort `30080` in front of GitLab Workhorse. The Envoy Gateway only routes `gitlab.gitlab.local`, which cannot match the playground's own hostname, and GitLab would otherwise redirect the browser to that hostname. The proxy marks requests as HTTPS, sets `Origin` to the forwarded host so the Rails origin check passes (the CSRF token is still checked), and rewrites absolute redirects to relative ones. The tab opens in a new window because GitLab refuses to render inside a frame.

The Gateway still serves `https://gitlab.gitlab.local` inside the cluster, as described above, and GitLab uses that URL in clone links and emails.

### Updating the playground

After changing the Makefile, Helm values, lab manifests, or the renderer, re-render and publish with `labctl` installed and authenticated:

```sh
labctl auth login
bash playground/iximiuz/publish.sh
```

`publish.sh` renders to a temporary file, runs `labctl playground update`, and only then records the result in `manifest.yaml`. Running sessions keep the old definition; start a new session to test a change:

```sh
labctl playground start gitlab-k8s-lab-1781ac3c
```

`python3 -m unittest discover -s tests` checks that the checked-in manifest matches the sources, that every lab file is embedded, and that the init tasks form a valid dependency graph.

To create a separate copy, for example on another account, render it under a new name, create it from the `k8s-omni` base, then set `DEFAULT_NAME` in `render_manifest.py` to the suffixed name that `labctl` prints:

```sh
python3 playground/iximiuz/render_manifest.py --name gitlab-k8s-lab --output /tmp/gitlab-k8s-lab.yaml
labctl playground create gitlab-k8s-lab --base k8s-omni -f /tmp/gitlab-k8s-lab.yaml
```

The playground is public: anyone can find, view, and start it (`accessControl` in the renderer). Each session runs on the account of whoever starts it, and generates its own credentials; the manifest contains none. Starting it needs a paid iximiuz plan: the free tier's per-playground limit is 5 vCPU and 8 GiB, which is too small for GitLab.

## Secrets and credentials

The sample values reference these Kubernetes Secret names and keys:

| Secret | Key | Used for |
| --- | --- | --- |
| `gitlab-postgresql-password` | `password` | External PostgreSQL user password |
| `gitlab-redis-password` | `password` | External Redis/Valkey authentication |
| `gitlab-gitaly-token` | `token` | Shared external Gitaly authentication token |
| `gitlab-object-storage` | `connection` | Rails object-storage connection configuration |
| `gitlab-registry-storage` | `config` | Container Registry storage configuration |
| `gitlab-backup-s3cfg` | `config` | S3-compatible Toolbox backup access |

These are references, not manifests containing credentials. Create the Secrets using a secrets controller or a secure out-of-repository workflow. Kubernetes Secret data is not automatically equivalent to a secrets manager; configure encryption at rest and restrict access with RBAC. The external Gitaly token must match the value configured on the Gitaly service.

The object-storage connection and Registry configuration formats depend on your provider. Follow GitLab's [external object storage](https://docs.gitlab.com/charts/advanced/external-object-storage/) documentation. GitLab's Helm backup utility uses a separate S3 configuration Secret; see [backup and restore](https://docs.gitlab.com/charts/backup-restore/).

## Upgrades and recovery

Do not automatically move the chart pin to the newest release. Before every upgrade, take and verify backups, review the target chart's release notes and GitLab upgrade path, and follow the official [Helm chart upgrade procedure](https://docs.gitlab.com/charts/installation/upgrade/). Back up the Rails secrets separately from the GitLab backup archive; a database/repository backup without the original secrets is not a complete recovery plan.

See [operations and recovery](./docs/operations.md) for routine checks, backups, restore planning, and release updates.

## Official documentation used

- [GitLab Helm chart overview and production architecture](https://docs.gitlab.com/charts/installation/)
- [Prerequisites and deployment options](https://docs.gitlab.com/charts/installation/tools/)
- [Helm deployment](https://docs.gitlab.com/charts/installation/deployment/)
- [Cloud provider guidance and chart sizing](https://docs.gitlab.com/charts/installation/cloud/)
- [Version mappings](https://docs.gitlab.com/charts/installation/version_mappings/)
- [Gateway API](https://docs.gitlab.com/charts/advanced/gateway-api/)
- [TLS configuration](https://docs.gitlab.com/charts/installation/tls/)
- [External PostgreSQL](https://docs.gitlab.com/charts/advanced/external-db/)
- [External Redis or Valkey](https://docs.gitlab.com/charts/advanced/external-redis/)
- [External Gitaly](https://docs.gitlab.com/charts/advanced/external-gitaly/)
- [External object storage](https://docs.gitlab.com/charts/advanced/external-object-storage/)
- [Storage planning](https://docs.gitlab.com/charts/installation/storage/)
- [Backup and restore](https://docs.gitlab.com/charts/backup-restore/)
- [Helm chart upgrades](https://docs.gitlab.com/charts/installation/upgrade/)
- [Reference architectures](https://docs.gitlab.com/administration/reference_architectures/)
