"""Lane A — clean-machine bootstrap smoke, run TWICE (CLEAN-INSTALL-HARDENING.md §3).

Why this exists
---------------
Three clean-machine install bugs reached the operator **serially** this session
because nothing ran the install on a fresh host, and nothing ran it **twice** to
prove idempotency:

  1. the ansible ``pip`` task lacked ``virtualenv_command: python3 -m venv`` — a
     clean host has ``python3-venv`` (stdlib ``python3 -m venv``) but NOT the
     standalone ``virtualenv`` pip package, so the pip module aborted demanding a
     ``virtualenv`` executable;
  2. a transient 0-MiB HF/Xet glitch on model pull (fixed in steps.py — not
     exercised here; this lane is the ansible side);
  3. a re-run over a venv left half-installed by a prior failed run hit
     ``OSError`` on ``site-packages/agmind-*.dist-info/INSTALLER<rand>.tmp`` +
     ``~gmind`` pip-backup leftovers → bootstrap ``rc=2`` / ``PLAY RECAP
     failed=1``. Fixed by a purge task + ``state: forcereinstall``.

What it runs
------------
A bare ``ubuntu:24.04`` container (``docker/Dockerfile.ubuntu-test``) that has
ansible-core + ``python3-venv`` but DELIBERATELY NOT the ``virtualenv`` pip pkg.
The repo is bind-mounted read-only at ``/work``. Inside, we run ansible against
**just the agmind_python role** via a throwaway localhost play — that role is the
exact recurring-failure path (venv create + pip tooling + purge + install + the
``agmind`` import smoke), and running only it keeps the lane hermetic and stable.

Running the *full* ``install.yml --tags bootstrap`` instead would drag in
``vars_prompt`` (domain + CF token), the Strix-Halo preflight hardware gate, and
the ``docker`` role (docker-in-docker) — none of which relate to this bug class
and all of which are flaky in a container. The three bugs all live in
``ansible/roles/agmind_python/tasks/main.yml``, so the role is the right unit.

Hermeticity
-----------
Everything happens inside an ephemeral container (``--rm``); the host's
``/opt/agmind`` and ``/var/lib/agmind`` are never touched. The install prefix is
``/opt/agmind-test`` *inside the container*. The repo mount is ``:ro``.

Four test cases
---------------
* ``test_clean_machine_bootstrap_first_run`` — fresh container, 1st role run
  succeeds end-to-end and ``import agmind`` works. The clean-machine smoke.
* ``test_clean_machine_bootstrap_rerun_is_idempotent`` — run the role TWICE in
  the same container; the 2nd run succeeds with NO ``OSError`` / ``Could not
  install`` / ``Rolling back`` in the output, and agmind still imports. The
  operator re-run scenario (bug-#3 class).
* ``test_clean_machine_bootstrap_recovers_corrupted_venv`` — between the two
  runs, inject the EXACT poison from bug #3 (a stale ``~gmind`` backup dir + a
  ``agmind-*.dist-info/INSTALLER<rand>.tmp``) and assert the purge +
  ``forcereinstall`` self-heal so the 2nd run still ``rc==0``.
* ``test_pip_module_requires_virtualenv_command_on_clean_host`` — proves bug #1:
  on this ``virtualenv``-less image the ansible ``pip`` module fails creating a
  venv WITHOUT ``virtualenv_command`` and succeeds WITH the role's
  ``python3 -m venv`` pin. (The role's explicit venv-create masks this on the
  happy path, so this drives the pip module directly — see that test's docstring.)

Marker / running locally
------------------------
``pytestmark = pytest.mark.integration`` excludes this file from the default
``pytest`` run. Run it with::

    pytest -m integration tests/install/test_clean_machine_bootstrap.py
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# integration: excluded from the default `-m "not integration"` lane (needs Docker + ~2min). Also
# backend_any so the marker-coverage guard (every test file carries a backend marker) passes.
pytestmark = [pytest.mark.integration, pytest.mark.backend_any]

# Repo root = two levels up from this file (tests/install/<file>).
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "docker" / "Dockerfile.ubuntu-test"
IMAGE_TAG = "agmind-ubuntu-test:ci"

# Install prefix INSIDE the container (never the host /opt/agmind).
CONTAINER_PREFIX = "/opt/agmind-test"

# Phrases that mark the clean-machine failures this lane guards against. Their
# absence in a successful run is part of the contract (a non-zero rc alone can
# hide a "changed_when: false / failed_when: false" swallow).
FAILURE_MARKERS = (
    "OSError",
    "Could not install packages",
    "Rolling back uninstall",
    "No such file or directory",
)

# Generous-but-bounded timeouts: the image build pulls ubuntu:24.04 + apt; the
# role run builds the agmind wheel from the read-only source + installs the dep
# tree into a fresh venv.
BUILD_TIMEOUT = 600  # seconds
RUN_TIMEOUT = 600  # seconds


def _docker_available() -> bool:
    """Return True when ``docker`` is on PATH and the daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


@pytest.fixture(scope="module")
def _skip_if_no_docker() -> None:
    """Skip the whole module unless a usable Docker daemon is present."""
    if not _docker_available():
        pytest.skip("Docker daemon not available — skipping clean-machine bootstrap lane")


@pytest.fixture(scope="module")
def ubuntu_test_image(_skip_if_no_docker: None) -> str:  # noqa: PT019
    """Build the bare-ubuntu test image once for the module; return its tag."""
    build = subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(DOCKERFILE),
            "-t",
            IMAGE_TAG,
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=BUILD_TIMEOUT,
    )
    if build.returncode != 0:
        pytest.fail(
            "docker build of Dockerfile.ubuntu-test failed (rc="
            f"{build.returncode}):\n{build.stdout}\n{build.stderr}"
        )
    return IMAGE_TAG


def _bootstrap_script(*, runs: int, inject_corruption_before_run: int | None = None) -> str:
    """Render the in-container shell that drives the agmind_python role.

    The script:
      1. creates the ``agmind`` system user the role's ``become_user`` needs;
      2. rsyncs the read-only repo mount ``/work`` to a writable ``/opt/src`` and
         writes a throwaway localhost play at ``/opt/src/ansible/.ci-play.yml``
         (so the role's ``agmind @ file://{{ playbook_dir }}/..`` source resolves
         to ``/opt/src``, a genuine pip-installable package root — see note below);
      3. runs the role ``runs`` times, optionally injecting the bug-#3 poison
         before run number ``inject_corruption_before_run``.

    ``set -e`` is intentionally NOT global: we want the ansible-playbook rc of
    EACH run surfaced as a labelled line so the python assertions can reason
    about which run failed and scan the combined output for FAILURE_MARKERS.

    Source-tree note: the agmind_python role hardcodes the install source as
    ``agmind @ file://{{ playbook_dir }}/..`` and symlinks ``audit_forbidden.py``
    from ``{{ playbook_dir }}/../scripts``. We mount the repo read-only at
    ``/work`` and ``rsync`` it to a writable ``/opt/src`` so the throwaway play
    can sit at ``/opt/src/ansible/.ci-play.yml`` — making ``playbook_dir/..`` ==
    ``/opt/src``, a genuine pip-installable copy of the package root. This
    faithfully exercises venv create + pip tooling + purge + forcereinstall +
    the import smoke without writing into the read-only mount.
    """
    corruption_block = ""
    if inject_corruption_before_run is not None:
        corruption_block = f"""
inject_corruption() {{
  # Reproduce the EXACT bug-#3 poison a prior failed pip run leaves behind:
  # a `~`-prefixed backup dir + a stale INSTALLER<rand>.tmp inside the agmind
  # dist-info. The role's purge task (+ state: forcereinstall) must self-heal it.
  sp="$(ls -d {CONTAINER_PREFIX}/venv/lib/python*/site-packages | head -1)"
  echo "INJECT: poisoning site-packages at $sp"
  mkdir -p "$sp/~gmind"
  echo "stale-backup" > "$sp/~gmind/__init__.py"
  di="$(ls -d "$sp"/agmind-*.dist-info 2>/dev/null | head -1)"
  if [ -z "$di" ]; then echo "INJECT-FAIL: no agmind dist-info to poison"; exit 90; fi
  : > "$di/INSTALLER1234.tmp"
  echo "INJECT: wrote $di/INSTALLER1234.tmp and $sp/~gmind"
}}

verify_purged() {{
  # The role's purge task must have removed BOTH poison artifacts. The exact
  # version-specific OSError the operator hit is not reliably reproducible on
  # every pip version via static injection, so we assert the FIX'S MECHANISM
  # directly: after the role runs, no `~`-prefixed backup dir and no stale
  # INSTALLER*.tmp may remain in site-packages. A role WITHOUT the purge task
  # leaves them → this check fails (it discriminates the fix from its absence).
  sp="$(ls -d {CONTAINER_PREFIX}/venv/lib/python*/site-packages | head -1)"
  leftover_backup="$(ls -d "$sp"/~* 2>/dev/null | head -1 || true)"
  leftover_tmp="$(ls "$sp"/*.dist-info/INSTALLER*.tmp 2>/dev/null | head -1 || true)"
  if [ -n "$leftover_backup" ] || [ -n "$leftover_tmp" ]; then
    echo "=== POISON NOT PURGED: backup=$leftover_backup tmp=$leftover_tmp ==="
  else
    echo "=== POISON PURGED ==="
  fi
}}
"""

    run_block_lines = []
    for i in range(1, runs + 1):
        if inject_corruption_before_run == i:
            run_block_lines.append("inject_corruption")
        run_block_lines.append(f'echo "=== AGMIND ROLE RUN {i} ==="')
        run_block_lines.append(
            "ansible-playbook -i localhost, -c local /opt/src/ansible/.ci-play.yml "
            f"-e agmind_install_dir={CONTAINER_PREFIX} -e agmind_user=agmind; "
            f'rc=$?; echo "=== AGMIND ROLE RUN {i} RC=$rc ==="'
        )
        # Independently prove the venv imports agmind after this run.
        run_block_lines.append(
            f'{CONTAINER_PREFIX}/venv/bin/python -c \'import agmind; print("IMPORT-OK", '
            f'agmind.__version__)\'; echo "=== IMPORT RUN {i} RC=$? ==="'
        )
        # After the run that followed the injection, assert the poison is gone.
        if inject_corruption_before_run == i:
            run_block_lines.append("verify_purged")
    run_block = "\n".join(run_block_lines)

    return f"""#!/bin/bash
set -u
{corruption_block}
# Replicate the bootstrap-role precondition the agmind_python role depends on:
# the `agmind` system user (its become_user) AND the install dir pre-created
# owned by agmind:agmind (bootstrap/tasks/main.yml creates the user with
# `home: {{{{ agmind_install_dir }}}}` and the dir `owner: agmind mode 0755`).
# Without an agmind-owned prefix the become_user venv-create hits EACCES under
# root-owned /opt. This is part of the contract bootstrap establishes before
# agmind_python runs, so staging it here keeps the role test faithful.
id agmind >/dev/null 2>&1 || useradd --system --shell /usr/sbin/nologin \
  --home-dir {CONTAINER_PREFIX} agmind
mkdir -p {CONTAINER_PREFIX}
chown agmind:agmind {CONTAINER_PREFIX}
chmod 0755 {CONTAINER_PREFIX}

# /work is the read-only repo mount. Copy it to a writable /opt/src so:
#  - we can drop a throwaway play next to the role, and
#  - the role's `agmind @ file://{{{{ playbook_dir }}}}/..` (== /opt/src) points at
#    a genuine pip-installable package root.
# Exclude the local .venv / VCS / caches to keep the copy fast and clean.
mkdir -p /opt/src
rsync -a --delete \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.pyc' --exclude='dist' --exclude='build' --exclude='.mypy_cache' \
  --exclude='.pytest_cache' --exclude='node_modules' \
  /work/ /opt/src/
chown -R agmind:agmind /opt/src

# Throwaway play that invokes ONLY the agmind_python role, as root with
# become (mirrors install.yml's `become: true` play that hosts this role).
cat > /opt/src/ansible/.ci-play.yml <<'PLAY'
---
- name: CI clean-machine bootstrap — agmind_python role only
  hosts: localhost
  gather_facts: false
  become: true
  roles:
    - role: agmind_python
PLAY

{run_block}
echo "=== BOOTSTRAP SCRIPT DONE ==="
"""


def _run_in_container(image: str, script: str) -> subprocess.CompletedProcess[str]:
    """Run ``script`` inside a fresh ``--rm`` container with the repo mounted :ro."""
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{REPO_ROOT}:/work:ro",
            "--entrypoint",
            "/bin/bash",
            image,
            "-c",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
    )


def _assert_role_run_succeeded(output: str, run_number: int) -> None:
    """Assert the labelled ansible run + import smoke for ``run_number`` were clean."""
    assert f"=== AGMIND ROLE RUN {run_number} RC=0 ===" in output, (
        f"agmind_python role run {run_number} did not exit 0:\n{output}"
    )
    assert f"=== IMPORT RUN {run_number} RC=0 ===" in output, (
        f"`import agmind` failed after role run {run_number}:\n{output}"
    )


def _assert_no_failure_markers(output: str) -> None:
    """Assert none of the clean-machine corruption signatures appear in output."""
    hit = [m for m in FAILURE_MARKERS if m in output]
    assert not hit, f"clean-machine failure marker(s) {hit} present in output:\n{output}"


def test_clean_machine_bootstrap_first_run(ubuntu_test_image: str) -> None:
    """Fresh clean host: the agmind_python role runs and ``import agmind`` works.

    The end-to-end clean-machine smoke: on a bare host with python3-venv but no
    ``virtualenv`` pip package, the role creates the venv, installs the agmind
    package from the ``file://`` source, and the import smoke passes. A new role
    that shells out to an unbootstrapped tool, or a broken pip source, fails here.
    (The ``virtualenv_command`` pin specifically is proven load-bearing by
    ``test_pip_module_requires_virtualenv_command_on_clean_host``.)
    """
    result = _run_in_container(ubuntu_test_image, _bootstrap_script(runs=1))
    assert result.returncode == 0, (
        f"container exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    output = result.stdout + result.stderr
    _assert_role_run_succeeded(output, 1)
    assert "IMPORT-OK" in output, f"agmind import smoke produced no IMPORT-OK line:\n{output}"


def test_clean_machine_bootstrap_rerun_is_idempotent(ubuntu_test_image: str) -> None:
    """Operator re-run: run the role TWICE; the 2nd run is clean (bug-#3 class).

    Asserts both runs succeed, agmind imports after each, and the combined
    output carries none of the OSError / "Could not install" / "Rolling back"
    signatures of the pip-reinstall-corrupts-venv failure.
    """
    result = _run_in_container(ubuntu_test_image, _bootstrap_script(runs=2))
    assert result.returncode == 0, (
        f"container exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    output = result.stdout + result.stderr
    _assert_role_run_succeeded(output, 1)
    _assert_role_run_succeeded(output, 2)
    _assert_no_failure_markers(output)


def test_clean_machine_bootstrap_recovers_corrupted_venv(ubuntu_test_image: str) -> None:
    """Inject the EXACT bug-#3 poison before the 2nd run; assert self-heal (bug #3).

    Between run 1 and run 2 we drop a ``~gmind`` pip-backup dir and a stale
    ``agmind-*.dist-info/INSTALLER<rand>.tmp`` into the venv's site-packages —
    the precise leftovers a prior interrupted pip run produced. The role's purge
    task + ``state: forcereinstall`` must recover so run 2 still exits 0, agmind
    imports, and the output carries none of the OSError / "Could not install" /
    "Rolling back" signatures.

    Discrimination: the version-specific ``OSError [Errno 2] INSTALLER<rand>.tmp``
    the operator hit is NOT reliably reproducible by static injection on every
    pip release (modern pip tolerates stray files), so a "2nd run rc==0" assert
    alone would pass even against a role WITHOUT the purge fix. We therefore also
    assert the fix's MECHANISM directly — after run 2 the purge task must have
    removed both poison artifacts (``=== POISON PURGED ===``). A role that drops
    the purge task leaves them and fails this assert. The companion parse-level
    guard in ``tests/ansible`` additionally pins the task + ``state:`` against
    silent removal.
    """
    result = _run_in_container(
        ubuntu_test_image,
        _bootstrap_script(runs=2, inject_corruption_before_run=2),
    )
    assert result.returncode == 0, (
        f"container exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    output = result.stdout + result.stderr
    # Prove the poison was actually written (else the test would pass vacuously).
    assert "INJECT: wrote" in output, f"corruption was not injected:\n{output}"
    _assert_role_run_succeeded(output, 1)
    _assert_role_run_succeeded(output, 2)
    _assert_no_failure_markers(output)
    # The fix's mechanism: the purge task removed the poison on the corrupted run.
    assert "=== POISON PURGED ===" in output, (
        "the agmind_python purge task did not remove the injected ~backup / "
        f"INSTALLER.tmp poison (bug-#3 fix regressed):\n{output}"
    )


# The pip-module options the role uses for both venv-pip tasks. The role's
# explicit ``command: python3 -m venv`` first-task pre-creates the venv, which
# MASKS the ansible ``pip`` module's ``virtualenv``-executable requirement on the
# happy path — so we must drive the pip module against a venv path that was NOT
# pre-created to prove the ``virtualenv_command`` pin is load-bearing on a host
# WITHOUT the ``virtualenv`` pip package (the exact clean-machine bug #1).
_PIP_VENV_PROBE = r"""#!/bin/bash
set -u
# Sanity: the image must NOT carry the standalone `virtualenv` executable, else
# this probe is vacuous (it would succeed regardless of the pin).
if command -v virtualenv >/dev/null 2>&1; then
  echo "PROBE-VACUOUS: image unexpectedly has a virtualenv executable"; exit 91
fi

# WITHOUT virtualenv_command: the pip module must create the venv itself and, on
# this image, fails demanding the `virtualenv` executable (reproduces bug #1).
cat > /tmp/probe_nopin.yml <<'PLAY'
---
- hosts: localhost
  gather_facts: false
  tasks:
    - name: pip into a fresh venv WITHOUT virtualenv_command (bug-#1 trigger)
      ansible.builtin.pip:
        name: ["pip"]
        virtualenv: /tmp/probe_venv_nopin
PLAY
ansible-playbook -i localhost, -c local /tmp/probe_nopin.yml >/tmp/nopin.out 2>&1
echo "=== NOPIN RC=$? ==="
# ansible escapes the JSON msg as: Failed to find required executable \"virtualenv\"
# so match the unambiguous unquoted prefix (+ the word virtualenv) rather than the
# escaped-quoted literal.
if grep -q 'Failed to find required executable' /tmp/nopin.out \
   && grep -q 'virtualenv' /tmp/nopin.out; then
  echo "=== NOPIN DEMANDED VIRTUALENV ==="
else
  echo "=== NOPIN DID NOT DEMAND VIRTUALENV ==="
fi

# WITH the role's pin: the pip module creates the venv via `python3 -m venv` and
# succeeds even though `virtualenv` is absent. This is the fix the role ships.
cat > /tmp/probe_pin.yml <<'PLAY'
---
- hosts: localhost
  gather_facts: false
  tasks:
    - name: pip into a fresh venv WITH virtualenv_command (role's fix)
      ansible.builtin.pip:
        name: ["pip"]
        virtualenv: /tmp/probe_venv_pin
        virtualenv_command: python3 -m venv
PLAY
ansible-playbook -i localhost, -c local /tmp/probe_pin.yml >/tmp/pin.out 2>&1
echo "=== PIN RC=$? ==="
echo "=== PROBE DONE ==="
"""


def test_pip_module_requires_virtualenv_command_on_clean_host(ubuntu_test_image: str) -> None:
    """Prove the ``virtualenv_command: python3 -m venv`` pin is load-bearing (bug #1).

    The role's happy path pre-creates the venv with an explicit ``command``, which
    hides the ansible ``pip`` module's ``virtualenv``-executable requirement. This
    probe drives the pip module directly against a fresh venv on the
    ``virtualenv``-less image:

    * WITHOUT ``virtualenv_command`` it MUST fail with ``Failed to find required
      executable "virtualenv"`` — the exact clean-machine bug #1; and
    * WITH the role's ``virtualenv_command: python3 -m venv`` it MUST succeed.

    Together these assert the pin is what keeps a clean host working, so a silent
    removal of the pin from the role can never pass unnoticed (the companion
    parse-level guard in ``tests/ansible`` asserts the role still carries it).
    """
    result = _run_in_container(ubuntu_test_image, _PIP_VENV_PROBE)
    output = result.stdout + result.stderr
    assert "PROBE-VACUOUS" not in output, f"probe ran on a host WITH virtualenv:\n{output}"
    # No virtualenv_command → pip module demands the virtualenv executable → fails.
    assert "=== NOPIN RC=0 ===" not in output, (
        f"pip module unexpectedly succeeded WITHOUT virtualenv_command:\n{output}"
    )
    assert "=== NOPIN DEMANDED VIRTUALENV ===" in output, (
        "pip module without virtualenv_command did not fail with the expected "
        f"'Failed to find required executable virtualenv':\n{output}"
    )
    # With the role's pin → pip module creates the venv via python3 -m venv → ok.
    assert "=== PIN RC=0 ===" in output, (
        f"pip module WITH virtualenv_command: python3 -m venv failed:\n{output}"
    )
