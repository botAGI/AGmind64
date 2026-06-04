# Offline / air-gap install

Install AGmind on a host with no internet by pre-staging the container images
and model files, then running the installer in offline mode.

> **Why a code flag is needed.** `docker save`/`docker load` strips an image's
> RepoDigest, so the digest-pinned `docker compose pull --policy missing` step
> would re-pull from the network and fail in an air-gap. Setting `AGMIND_OFFLINE=1`
> turns that step into a `--policy never` no-op (the deploy already uses
> `up --pull never`), so the preloaded images are used. Verify a loaded image by
> its **image ID** (`docker image inspect`), not RepoDigest (which is empty after
> load).

## 1. Bundle (on an internet-connected host)

Generate the exact pinned image list from the descriptor catalog — never paste a
hardcoded list, it rots when a digest changes:

```bash
# All images, or scope to the profiles you will deploy. The module path differs by
# install mode: a pip/wheel install exposes it as `agmind.scripts.bundle_manifest`;
# a source checkout (`pip install -e .`) exposes it as `scripts.bundle_manifest`.
python -m agmind.scripts.bundle_manifest --profile core,rag,observability > images.txt \
  || python -m scripts.bundle_manifest --profile core,rag,observability > images.txt

# Save them to a single tar (the helper probes the right module path for you):
scripts/bundle-images.sh --profile core,rag,observability -o agmind-images.tar
```

Copy the model files you selected. The installer expects the exact filenames
referenced by `AGMIND_MODEL_FILE` / `AGMIND_EMBED_FILE` / `AGMIND_RERANK_FILE`
in the generated `.env` (e.g. the default trio is roughly 22 GiB):

```bash
ls /var/lib/agmind/models/   # the *.gguf files to carry over
```

## 2. Transfer and load (on the air-gap host)

```bash
docker load -i agmind-images.tar

# Place the GGUFs where ModelDownloadStep will detect them (and skip download):
sudo mkdir -p /var/lib/agmind/models
sudo cp *.gguf /var/lib/agmind/models/
```

## 3. Install offline

```bash
export AGMIND_OFFLINE=1
agmind install --no-tui --domain <domain> --profile core,rag,observability,ui
```

With `AGMIND_OFFLINE=1` the image-pull step is a `--policy never` no-op and the
model-download step reuses the prestaged GGUFs (it already skips download when a
valid file is present). If an image was not loaded, the deploy fails fast with a
clear "image not found" rather than hanging on the network.

> Bundle size warning: the full image set plus the default GGUF trio is tens of
> GiB. Scope the image list with `--profile` to only what you deploy.
