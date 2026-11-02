# Operations and recovery

This guide covers the routine work around the GitLab Helm release. Adapt backup schedules, retention, alerting, and recovery objectives to your organization's requirements.

## Health and monitoring

- Check `helm status gitlab -n gitlab`, `kubectl get pods,services -n gitlab`, and pod events after installs and upgrades.
- Monitor the Kubernetes workloads and the external PostgreSQL, Redis/Valkey, Gitaly, and object-storage services. A healthy set of Pods alone does not establish that GitLab's dependencies are healthy.
- In the disposable lab only, RustFS provides object storage and Prometheus scrapes GitLab components. Both are in-cluster demonstrations, not substitutes for durable object storage or a production monitoring stack.
- Alert on persistent-volume capacity where applicable, database and Redis availability, Gitaly health, object-storage errors, certificate expiry, and failed backup jobs.
- Confirm the Gateway has an external address and that DNS, HTTPS, SSH, Registry, and KAS access work as intended.

## Backups

GitLab's chart provides a Toolbox pod and `backup-utility`. Configure the Toolbox with the S3-compatible backup Secret referenced in `helm/values.yaml`, and ensure the backup and temporary buckets exist. Set a scheduled backup and retention policy outside Helm; store backups outside the cluster and test that the backup destination is recoverable.

Follow the current [GitLab Helm backup procedure](https://docs.gitlab.com/charts/backup-restore/backup/) for the exact command and current chart behavior. Before relying on it:

- Confirm the Toolbox has enough temporary disk for the backup or restore operation.
- Back up the GitLab Rails secrets separately and securely. The backup archive does not include them.
- Include the external database, Gitaly repositories, and object-storage data in a consistent backup and recovery plan. Do not assume the Helm backup archive copies every external bucket.
- Test restoration in a separate environment and retain the original backup archive name.

The [restore procedure](https://docs.gitlab.com/charts/backup-restore/restore/) documents the supported sequence and compatibility constraints. A restore is not complete until the application, repositories, object data, and secrets have been verified together.

The lab's PostgreSQL, Redis, RustFS, and Gitaly data use `emptyDir` volumes and are lost when their pods are recreated. Do not store data that needs to survive a playground restart or pod replacement.

## Upgrades

The `helm/chart-version` file is the single chart version pin. To upgrade:

1. Read the [version mapping](https://docs.gitlab.com/charts/installation/version_mappings/) and chart changelog for the target release.
2. Review GitLab's required upgrade path and the [Helm chart upgrade guide](https://docs.gitlab.com/charts/installation/upgrade/). Do not skip required GitLab upgrade stops.
3. Take a backup and verify that the Rails secrets and external-service recovery procedures are available.
4. Update the pin, run `make validate`, then use `make deploy` during a maintenance window appropriate to the release.
5. Verify migrations, application health, background jobs, integrations, and data access after rollout.

Zero-downtime upgrades require additional replica and rolling-update configuration from GitLab's [recommended deployment settings](https://docs.gitlab.com/charts/installation/upgrade/#recommended-deployment-settings). The starter values do not claim zero-downtime behavior.

PostgreSQL major-version upgrades are separate from GitLab chart upgrades. Check the [supported PostgreSQL range](https://docs.gitlab.com/install/requirements/#postgresql) for the target GitLab version and use a supported major-upgrade procedure, such as a verified logical backup and restore. Do not change a PostgreSQL image tag against an existing data directory and assume the data format is compatible.

## Storage and uninstall safety

Plan storage before the first deployment. GitLab recommends dynamic provisioning where available, fast disks for repositories, and a storage class with `Retain` reclaim policy. Do not uninstall the Helm release as a cleanup shortcut: review the chart's storage and data-retention behavior first, and preserve external services, object buckets, Secrets, and backups.
