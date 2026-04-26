# Release procedure

Step-by-step checklist for cutting a public release of Mimir. Lives
under `operator-notes/` because it documents one specific maintainer's
workflow (the author's GitHub + PyPI accounts) — others can adapt.

## Pre-flight (do once, then revisit each release)

- [ ] PyPI account exists with API token configured locally
      (`uv` reads `~/.pypirc` or `UV_PUBLISH_TOKEN` env var).
- [ ] GitHub repo settings → *Branches* → `main` requires CI green
      before merge.
- [ ] GitHub repo settings → *Actions* → workflows enabled.
- [ ] LICENSE file present and matches the classifier in
      `pyproject.toml`.

## Per-release checklist

### 1. Pre-merge sanity

```bash
cd /path/to/mimir-mcp

# Working tree clean
git status                              # nothing to commit

# Suite green locally
uv run --extra test pytest tests/ -q
# Expected: 313 passed + 2 skipped (or higher)

# End-to-end smoke
uv run --extra test pytest tests/test_integration_e2e.py -q
# Expected: 4 passed

# Dry-run prints expected banner
uv run python router.py --dry-run
# Expected: "[mimir] router — profile: default"
```

### 2. Version + changelog

- [ ] Bump `version` in `pyproject.toml` (semver: 0.1.0 → 0.1.1 for
      patches, 0.2.0 for new features, 1.0.0 when API stabilises).
- [ ] Move "Unreleased" content in `CHANGELOG.md` to a new section
      with the date and version: `## [0.X.Y] — YYYY-MM-DD`.
- [ ] Add a fresh empty `## [Unreleased]` block at the top.

### 3. Merge

```bash
# From the working branch
git checkout refactor/generify-naming   # or whatever branch
git rebase main                         # if needed
git checkout main
git merge --ff-only refactor/generify-naming
```

If `--ff-only` rejects, you have divergence — investigate before
forcing.

### 4. Tag + push

```bash
git tag -a v0.X.Y -m "v0.X.Y"
git push origin main
git push origin v0.X.Y
```

CI runs on the push to main. Wait for green.

### 5. Build + publish to PyPI

```bash
# Clean any stale dist/
rm -rf dist build *.egg-info

# Build sdist + wheel
uv build

# Confirm the artefacts
ls dist/
# mimir_mcp-0.X.Y-py3-none-any.whl
# mimir_mcp-0.X.Y.tar.gz

# Publish
uv publish
```

### 6. Verify on a clean machine

```bash
# In a throwaway venv
uv venv /tmp/mimir-test
source /tmp/mimir-test/bin/activate
pip install mimir-router-mcp==0.X.Y
mimir --dry-run
# Expected: "[mimir] router — profile: default"
```

### 7. GitHub release

- Web UI → *Releases* → *Draft a new release* → tag `v0.X.Y`.
- Title: `Mimir v0.X.Y`.
- Body: copy the matching section from `CHANGELOG.md`.
- *Generate release notes* helps but the changelog is authoritative.

### 8. Promotion (optional, opt-in)

- [ ] Post in r/mcp with the angle from
      `docs/operator-notes/cutover/README.md` (Fase 9c).
- [ ] If first release: also r/LocalLLaMA, link the README and the
      quickstart.
- [ ] Tweet / Bluesky if the maintainer feels like it. Not required.

## Rollback (if PyPI release is broken)

PyPI does not allow deleting versions, only **yanking**:

```bash
# Mark the version as "do not install" — preserves the metadata,
# blocks new installs but keeps existing ones working.
uv publish --yank --reason "broken release, see issue #N" \
    --version 0.X.Y mimir-mcp
```

Then publish a 0.X.(Y+1) with the fix and a CHANGELOG note.

## When *not* to release

- Tests are red on the target branch.
- A breaking change is going in but the version is a patch bump.
- The maintainer is tired or distracted. Releases are forever; better
  to wait a day.

## Signing (deferred)

Not currently signing artefacts. When PyPI's Trusted Publishers /
Sigstore support stabilises further, set it up via GitHub Actions.
Documented as deferred so future maintainers know it's a TODO, not
an oversight.
