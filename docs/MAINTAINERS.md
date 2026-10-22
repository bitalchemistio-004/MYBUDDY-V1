# For maintainers: opening the project to the public

The repository contents are ready (licence, contributing guide, security policy, code of conduct, governance, issue and pull
request templates, CODEOWNERS, CI). A few things can only be switched on in GitHub's settings by the owner. This is the list.

## Before making it public

- [ ] **Read the history once.** The commit history contains earlier working notes and tooling output. The current files were
      cleaned (no keys, no personal paths, no AI session logs), but old commits still hold what was in them. If anything in the
      history should not be public, say so before the switch: it is much easier before than after.
- [ ] **Check your `.env` was never committed** (it has not been: it is ignored and the scan found no keys).
- [ ] **Make and keep a release key** (see [BUILD.md](BUILD.md)) if you will publish APKs.
- [ ] **Decide the artwork's licence** if you want it different from the code (the buddies are covered by GPL-3.0 as it stands).

## Settings to switch on (GitHub, repository Settings)

| Where | Setting | Why |
|---|---|---|
| General | Description, website, **topics** (`android`, `esp32`, `tauri`, `kotlin`, `voice-assistant`, `privacy`, `local-ai`) | So people can find it |
| General | Features: **Issues** on, **Discussions** on | Bugs and ideas go to issues; questions and show-and-tell to discussions |
| Branches | Protect `main`: require a pull request, require the **CI** checks, require review from code owners, block force pushes | Nothing lands without tests and a second pair of eyes |
| Code security | **Private vulnerability reporting** on, **secret scanning** and **push protection** on, **Dependabot alerts** on | Matches `SECURITY.md`, catches an accidental key before it is public |
| Labels | `bug`, `enhancement`, `question`, `good first issue`, `help wanted`, `needs-triage`, `safety`, `android`, `desktop`, `hardware`, `docs` | The templates apply some of these |
| Pages / pinned | Pin an issue "Start here: good first issues" (the list is in [GOOD_FIRST_ISSUES.md](GOOD_FIRST_ISSUES.md)) | Gives a newcomer a first step |

## Keeping it observed

- **CI** (`.github/workflows/ci.yml`) runs the page tests, the Android unit tests and a debug build on every push and pull
  request. Look at the first run on GitHub and fix whatever it finds: it was written to match the PC build and has not run there yet.
- **Dependabot** opens a pull request when a dependency has a new version.
- **CODEOWNERS** asks the right person to review. Add owners per area as people join ([GOVERNANCE.md](../GOVERNANCE.md)).
- **The changelog** ([CHANGELOG.md](../CHANGELOG.md)) says what changed in words a user can follow, before each release.
- **Triage weekly:** label new issues, answer questions, close what is answered, keep a few `good first issue` open.

## A release, step by step

1. Tests are green on `main`.
2. Update [CHANGELOG.md](../CHANGELOG.md).
3. Build the release APK with the release key (`./gradlew :app:assembleRelease`).
4. Tag it, create the GitHub release from the changelog, attach the APK and its checksum.
5. Note the release in the ledger.
