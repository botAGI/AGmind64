# Changelog

All notable changes to AGmind (x86 / AMD Strix Halo) are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
AGmind is pre-1.0; until the first tagged release everything lands under
[Unreleased].

## [Unreleased]

### Added
- `SECURITY.md` (private vulnerability reporting) and this changelog.
- CI guard tests: every test file must carry a backend marker
  (`tests/test_marker_coverage.py`); the `compose-validate` env must cover every
  required descriptor secret (`tests/services/test_ci_compose_secrets.py`).
- `agmind.cli.install_state` — testable `--from-state` resolver extracted from
  the install CLI handler.

### Changed
- Atomic secret-file writes create the temp at the target mode (`O_CREAT|O_EXCL`),
  closing the umask race; `write_env`/`write_private_text` now share
  `core.files.write_text_atomic`.
- CI: base Docker image built once (dedicated job) instead of per backend;
  `timeout-minutes` on heavy jobs; `release-drafter` moved to `ubuntu-latest`
  with a fork-PR guard.
- `typer` floored at `>=0.26` (vendored click); tests use `typer.testing`.

### Fixed
- 62 tests (service-selection closure, component contracts, topology, retrieval,
  proxmox-exporter) were silently skipped by the CI lane and are now gated.
- `agmind gc models` refuses to delete when a service descriptor fails to parse
  (previously could delete in-use model files on a single bad YAML).
- Install streaming helpers reap the child process and pipes on error.
- Bind-aware host-port conflict detection; service selection rejects unknown
  service names; corrected stale postgres/redis version-hold reasons.
