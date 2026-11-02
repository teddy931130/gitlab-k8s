CHART_VERSION ?= $(shell tr -d '\n' < helm/chart-version)
ENVOY_GATEWAY_VERSION ?= 1.9.3
NAMESPACE ?= gitlab
RELEASE ?= gitlab
GREP ?= /usr/bin/grep
CHART_REPOSITORY := https://charts.gitlab.io/
CHART := gitlab/gitlab
BASE_VALUES := helm/values.yaml
EXAMPLE_VALUES := helm/values.production.example.yaml
PRODUCTION_VALUES := helm/values.production.yaml
LAB_VALUES := helm/values.lab.yaml

.PHONY: validate check-production-config namespace gateway-crds deploy deploy-lab \
	lab-dependencies lab-object-storage lab-gitlab status

validate:
	helm repo add gitlab $(CHART_REPOSITORY) --force-update
	helm repo update gitlab
	helm template $(RELEASE) $(CHART) \
		--version "$(CHART_VERSION)" \
		--namespace "$(NAMESPACE)" \
		--values $(BASE_VALUES) \
		--values $(EXAMPLE_VALUES) \
		> /dev/null
	helm template $(RELEASE) $(CHART) \
		--version "$(CHART_VERSION)" \
		--namespace "$(NAMESPACE)" \
		--values $(BASE_VALUES) \
		--values $(LAB_VALUES) \
		> /dev/null
	@echo "GitLab chart $(CHART_VERSION) rendered successfully."

check-production-config:
	@test -s "$(PRODUCTION_VALUES)" || { \
		echo "Missing $(PRODUCTION_VALUES). Copy $(EXAMPLE_VALUES) and fill in the cluster-specific values."; \
		exit 1; \
	}
	@if $(GREP) -nE 'example\.invalid|CHANGE_ME|REPLACE_ME' "$(PRODUCTION_VALUES)"; then \
		echo "Replace all example placeholders in $(PRODUCTION_VALUES) before deploying."; \
		exit 1; \
	fi
	@$(GREP) -qE '^    domain:[[:space:]]*[^#[:space:]]' "$(PRODUCTION_VALUES)" || { \
		echo "Set global.hosts.domain in $(PRODUCTION_VALUES)."; \
		exit 1; \
	}
	@test "$$($(GREP) -Ec '^    host:[[:space:]]*[^#[:space:]]' "$(PRODUCTION_VALUES)")" -ge 2 || { \
		echo "Set both global.psql.host and global.redis.host in $(PRODUCTION_VALUES)."; \
		exit 1; \
	}
	@$(GREP) -qE '^        hostname:[[:space:]]*[^#[:space:]]' "$(PRODUCTION_VALUES)" || { \
		echo "Set global.gitaly.external[0].hostname in $(PRODUCTION_VALUES)."; \
		exit 1; \
	}
	@$(GREP) -qE '^  email:[[:space:]]*[^#[:space:]]' "$(PRODUCTION_VALUES)" || { \
		echo "Set certmanager-issuer.email in $(PRODUCTION_VALUES)."; \
		exit 1; \
	}

namespace:
	kubectl create namespace "$(NAMESPACE)" --dry-run=client -o yaml | kubectl apply -f -

gateway-crds:
	helm show crds oci://registry-1.docker.io/envoyproxy/gateway-helm \
		--version "$(ENVOY_GATEWAY_VERSION)" | \
		kubectl apply --server-side -f -
	kubectl wait --for=condition=Established \
		crd/gatewayclasses.gateway.networking.k8s.io \
		crd/gateways.gateway.networking.k8s.io \
		crd/httproutes.gateway.networking.k8s.io \
		crd/tcproutes.gateway.networking.k8s.io \
		crd/backendtrafficpolicies.gateway.envoyproxy.io \
		crd/envoyproxies.gateway.envoyproxy.io \
		--timeout=120s

deploy: check-production-config
	$(MAKE) gateway-crds
	helm repo add gitlab $(CHART_REPOSITORY) --force-update
	helm repo update gitlab
	helm template $(RELEASE) $(CHART) \
		--version "$(CHART_VERSION)" \
		--namespace "$(NAMESPACE)" \
		--values $(BASE_VALUES) \
		--values $(PRODUCTION_VALUES) \
		> /dev/null
	helm upgrade --install $(RELEASE) $(CHART) \
		--version "$(CHART_VERSION)" \
		--namespace "$(NAMESPACE)" \
		--create-namespace \
		--timeout 20m \
		--wait \
		--values $(BASE_VALUES) \
		--values $(PRODUCTION_VALUES)

# deploy-lab runs the three lab stages in order. The Iximiuz playground runs
# each stage as its own init task, so startup progress is visible per stage.
deploy-lab:
	$(MAKE) lab-dependencies
	$(MAKE) lab-object-storage
	$(MAKE) lab-gitlab

lab-dependencies: gateway-crds
	kubectl create namespace "$(NAMESPACE)" --dry-run=client -o yaml | kubectl apply -f -
	kubectl apply -f k8s/lab/dependencies.yaml

lab-object-storage:
	NAMESPACE="$(NAMESPACE)" python3 k8s/lab/configure-object-storage.py
	kubectl apply -f k8s/lab/object-storage.yaml
	kubectl wait --for=condition=Available deployment/gitlab-minio \
		--namespace "$(NAMESPACE)" --timeout=5m
	kubectl delete job gitlab-object-storage-buckets \
		--namespace "$(NAMESPACE)" --ignore-not-found
	kubectl apply -f k8s/lab/object-storage-buckets.yaml
	kubectl wait --for=condition=Complete job/gitlab-object-storage-buckets \
		--namespace "$(NAMESPACE)" --timeout=5m

lab-gitlab:
	helm repo add gitlab $(CHART_REPOSITORY) --force-update
	helm repo update gitlab
	helm template $(RELEASE) $(CHART) \
		--version "$(CHART_VERSION)" \
		--namespace "$(NAMESPACE)" \
		--values $(BASE_VALUES) \
		--values $(LAB_VALUES) \
		> /dev/null
	helm upgrade --install $(RELEASE) $(CHART) \
		--version "$(CHART_VERSION)" \
		--namespace "$(NAMESPACE)" \
		--create-namespace \
		--timeout 20m \
		--wait \
		--values $(BASE_VALUES) \
		--values $(LAB_VALUES)

status:
	helm status $(RELEASE) --namespace "$(NAMESPACE)"
	kubectl get pods,services --namespace "$(NAMESPACE)"
