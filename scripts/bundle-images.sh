#!/usr/bin/env bash
# Save AGmind's pinned container images to a tar for an offline / air-gap install.
# The image list is generated from the descriptor catalog (scripts/bundle_manifest.py)
# so it never rots. See docs/installation/offline-install.md.
set -euo pipefail

profile=""
output="agmind-images.tar"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      profile="$2"
      shift 2
      ;;
    -o | --output)
      output="$2"
      shift 2
      ;;
    -h | --help)
      echo "usage: $0 [--profile core,rag] [-o agmind-images.tar]"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

manifest_args=()
if [ -n "$profile" ]; then
  manifest_args+=(--profile "$profile")
fi

# The module path differs by install mode: a wheel/pip install exposes it as
# `agmind.scripts.bundle_manifest`, a source checkout as `scripts.bundle_manifest`. Probe
# importability (no side effects) and run the one that resolves, so a REAL runtime error in
# the manifest still surfaces instead of being masked by a blind `||` fallback.
if python -c 'import agmind.scripts.bundle_manifest' 2>/dev/null; then
  bundle_mod="agmind.scripts.bundle_manifest"
else
  bundle_mod="scripts.bundle_manifest"
fi
mapfile -t images < <(python -m "$bundle_mod" "${manifest_args[@]}")
if [ "${#images[@]}" -eq 0 ]; then
  echo "no images resolved from the catalog" >&2
  exit 1
fi

echo "saving ${#images[@]} images -> ${output}" >&2
docker save -o "$output" "${images[@]}"
echo "done: ${output}" >&2
