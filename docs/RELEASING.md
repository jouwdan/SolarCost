# Maintaining and releasing SolarCost

SolarCost is one HACS **Integration** repository. Its Python integration, JavaScript cards, translations, templates and icon all live under `custom_components/solarcost`. The card is registered by the integration; it does not need a separate HACS Dashboard repository or release.

## Validation

[Validate](../.github/workflows/validate.yml) runs on branch pushes, pull requests, manual requests and a weekly schedule:

- Python accounting tests and Ruff checks.
- Node card tests.
- An installable ZIP built from committed integration files, checked for its version, required assets, safe layout and matching file contents.
- Setup, configuration, persistence and report smoke tests in Home Assistant 2026.9.0, the declared minimum version.
- Official `home-assistant/actions/hassfest` and `hacs/action` validation. No HACS checks are ignored.

The HACS action reads the repository revision from GitHub. It is explicitly given the pull request's head SHA or the running commit SHA so validation also covers the exact revision during a release. GitHub Actions dependency updates are proposed monthly by Dependabot.

Run the local checks from the repository root with Python 3.12+, Node 22 and Docker installed:

```sh
python -m pip install ruff==0.16.6
python -m unittest discover -s tests -v
ruff check .
ruff format --check .
node tests/test_card.mjs
docker run --rm -v "$PWD:/work:ro" -w /work --entrypoint python ghcr.io/home-assistant/home-assistant:2026.9.0 tests/ha_smoke.py
docker run --rm -v "$PWD:/github/workspace" ghcr.io/home-assistant/hassfest
```

After committing, check the archive that would be released:

```sh
mkdir -p dist
git archive --format=zip --output=dist/solarcost.zip --add-file=LICENSE HEAD:custom_components/solarcost
python tests/check_release.py dist/solarcost.zip
```

The archive check intentionally fails if the working integration differs from the committed package. For visual changes, serve the repository with `python -m http.server 8766` and open `http://localhost:8766/tests/card_preview.html`, then verify the card in Home Assistant. The preview uses sample data and runs interaction checks.

## Publish a release

1. Make changes on a branch and update `custom_components/solarcost/manifest.json` with a new stable `major.minor.patch` version. The manifest is the single version source; there is no separate card version to bump. Increase `hacs.json`'s `homeassistant` requirement and the smoke-test image together if the minimum supported Home Assistant version changes.
2. Commit using conventional messages, open a pull request, and merge into `main` after **Validate** passes. Check the card, setup and upgrades in a development Home Assistant instance when changing runtime behavior.
3. Open **GitHub → Actions → Release → Run workflow**, select **main**, and run it. Do not create a tag or release beforehand.
4. The [Release workflow](../.github/workflows/release.yml) reruns all validation on that commit. Only after every job passes does it publish `v<manifest version>` and a full GitHub release, attach the tested `solarcost.zip` and `solarcost.zip.sha256`, and include installation instructions, generated release notes and a link to the validation run.
5. Open the release and check the notes and assets. Install the published version through HACS in a development instance, restart Home Assistant, then verify the integration and card.

The workflow only publishes from `main`, serializes release runs, and never moves an existing tag. An existing tag must point at the validated commit; an already published version fails instead of replacing its release. A failed validation publishes nothing. Fix code failures in a new commit and rerun; for a transient infrastructure failure, rerun the failed jobs. If a release was already published, bump the version for further changes.

Only the publishing job has `contents: write`; checks have read access. GitHub's built-in `GITHUB_TOKEN` is sufficient. No personal access token, Home Assistant credentials or local filesystem access is needed by CI. Ensure Actions are enabled for the repository and repository/organization policies permit the workflow's requested token permissions.

HACS uses the integration directory from the released GitHub source. We deliberately leave `zip_release` unset in `hacs.json`: the attached flat ZIP is for convenient manual installation, while HACS uses its standard source download. The ZIP has `manifest.json` at its root, not another `custom_components` directory. It contains the committed integration files and MIT license; no tests, screenshots, development files or household data are packaged.

## Deploy and roll back

Releasing makes an update available; it does not automatically install into anyone's Home Assistant. Users download it in HACS, restart Home Assistant and refresh their dashboard. Manual users extract `solarcost.zip` into `config/custom_components/solarcost/`.

Create a Home Assistant backup before updating. SolarCost keeps its ledger under `config/solarcost/`, outside the integration's replaceable code directory. HACS updates preserve the configuration entry and this history. Do not delete or re-add the integration to update it.

If a release breaks an installation, select a previous release in HACS's download controls or install that version manually, then restart Home Assistant and refresh the browser. If the failed update changed stored-data formats, restore the pre-update backup with the previous code. Publish a new patch version to fix a bad release; never replace a published tag with different code.

## Submit to the HACS default list

Custom-repository installation works before default-list approval. Default inclusion is a separate review by HACS; a passing workflow does not automatically submit or list SolarCost.

1. Verify that `jouwdan/SolarCost` is public, has Issues enabled, a description, and relevant topics such as `home-assistant`, `hacs`, `solar-energy` and `energy-monitoring`. Install the latest release as an **Integration** custom repository and confirm it works.
2. Run **Release** successfully. HACS requires passing HACS and Hassfest actions, no ignored checks, and a full GitHub release created after validation. Keep the release URL and successful action-run URL for the submission.
3. Fork [hacs/default](https://github.com/hacs/default) into your personal GitHub account. Create a new branch from its `master` branch, for example `add-solarcost`.
4. Edit the root [`integration`](https://github.com/hacs/default/blob/master/integration) JSON array and insert `"jouwdan/SolarCost"` in sorted order. Keep the repository's exact spelling and capitalization. SolarCost belongs in `integration`, including its bundled card.
5. Open a pull request against `hacs/default:master`, allow maintainer edits, complete the current checklist, and supply the release, successful HACS and successful Hassfest links. Both check links can point to the completed Release run, which includes those validation jobs. Wait for the checks and maintainer review; once merged, HACS picks it up in a subsequent scheduled scan.

The bundled `custom_components/solarcost/brand/icon.png` satisfies the current HACS brand check. A separate submission to `home-assistant/brands` is not required for this integration. SolarCost supports configurable tariffs and currencies rather than a specific country, so `country` is omitted from `hacs.json`.

Use the live [HACS submission guide](https://www.hacs.xyz/docs/publish/include/), [integration requirements](https://www.hacs.xyz/docs/publish/integration/), [HACS action documentation](https://www.hacs.xyz/docs/publish/action/) and [current pull request template](https://github.com/hacs/default/blob/master/.github/PULL_REQUEST_TEMPLATE.md) when submitting; their requirements can change. The current [brand validator](https://github.com/hacs/integration/blob/main/custom_components/hacs/validate/brands.py) accepts bundled icons.
