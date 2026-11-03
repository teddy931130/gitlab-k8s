#!/usr/bin/env bash
# Render the playground manifest and publish it with `labctl playground update`.
#
# render_manifest.py embeds the lab files (Makefile, Helm values, k8s/lab
# manifests) in the manifest, so one update ships every change. The manifest is
# rendered to a temporary file and recorded in manifest.yaml only after the
# update succeeds. Running sessions keep the old definition; start a new
# session to test the change.
#
#   PLAYGROUND_NAME  default: the name in render_manifest.py (DEFAULT_NAME)
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
manifest="${script_dir}/manifest.yaml"

die() { echo "$*" >&2; exit 1; }

command -v labctl >/dev/null || die "labctl is required: https://github.com/iximiuz/labctl"
labctl auth whoami >/dev/null 2>&1 || die "run: labctl auth login"

next_manifest="$(mktemp "${manifest}.next.XXXXXX")"
trap 'rm -f "$next_manifest"' EXIT
render_args=(--output "$next_manifest")
[ -n "${PLAYGROUND_NAME:-}" ] && render_args+=(--name "$PLAYGROUND_NAME")
python3 "${script_dir}/render_manifest.py" "${render_args[@]}" >/dev/null
playground_name="$(sed -n 's/^name: "\(.*\)"$/\1/p' "$next_manifest")"
[ -n "$playground_name" ] || die "could not read the playground name from the rendered manifest"

echo "== update playground ${playground_name} =="
labctl playground update "$playground_name" -f "$next_manifest"
chmod 644 "$next_manifest"
mv "$next_manifest" "$manifest"

echo "Done:"
echo "  playground: https://labs.iximiuz.com/playgrounds/${playground_name}"
echo "  try it in a new session: labctl playground start ${playground_name}"
