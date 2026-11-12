# Contributing to Alcor

Contributions of all sizes are welcome, from documentation fixes to new features.

## Language policy

To keep the project accessible to a global community, **please write issues,
pull requests, code comments, and commit messages in English.**

> 为方便全球开发者协作，请尽量使用**英文**提交 issue、PR、代码注释与
> commit message。不必担心英文不完美——表达清楚即可，工具翻译也完全没问题。感谢理解 ❤️

## Reporting issues

Found a bug or have an idea? [Open an issue](https://github.com/starpickerr/Alcor/issues/new/choose).

Before opening one, please search existing issues (including closed ones) to
avoid duplicates, and make sure you're on the latest version.

## Submitting a pull request

1. **Fork** the repo and create a branch from `develop`
   (e.g. `feat/web-search`, `fix/telegram-reconnect`). Use `feat/` for new
   features and `fix/` for bug fixes; use `docs/`, `refactor/`, or `chore/`
   when those better describe the change.
2. Make your change. Keep it focused — one logical change per PR.
3. Follow the existing code style. Write comments and docstrings in English.
4. Run the app locally to confirm your change works.
5. Open a PR targeting `develop`, with a clear title and a short description of
   **what** and **why**. If your branch depends on another open PR, mention that
   dependency and update the target after the earlier PR merges.

`develop` holds integrated work before release. `main` is the release branch:
when a version is ready, open a PR from `develop` into `main`, verify the
release, and tag the release from `main`.

We keep the bar friendly: clear, focused, and working is enough. Maintainers are
happy to help polish details during review.

### Commit & PR titles

Use `<type>(<scope>): <description>` for commits. Write the description in English as a short imperative phrase.

```text
feat(calendar): Add a weekly agenda view
fix(apple-sync): Preserve recurring reminders on reconnect
docs(native-product): Update desktop setup instructions
```

## Development setup

Use Node.js 24.9 or later and Python 3.9 or later. On macOS, install Xcode Command Line Tools to build the native helpers. From the repository root run:

```sh
npm ci --ignore-scripts
npm run product:build
npm run dev
```

The preview runs in an isolated DSH profile. Plugin code lives in `packages/`. The [native product guide](product/README.md) covers desktop installation, packaging and compatibility.

## Quality checks

Run `npm test` for the native product suite, or `make check` to build and test together. Desktop changes should also be checked in the official client with an isolated profile. For changes to the legacy Python/Electron application, use `make check-legacy` and the [legacy desktop guide](desktop/README.md).

`make audit` runs the complete current product build and suite. The retained Python/Electron full audit is `make audit-legacy`; its maintenance workflow still runs when that runtime changes. See the [CI ownership and coverage map](docs/development/ci-coverage.md) for platforms, triggers, retained tests and validation limits.

## Code of conduct

Be respectful and constructive. We want Alcor to be a welcoming place for everyone.
