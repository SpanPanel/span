# Development Notes

## Developer Prerequisites

- Poetry
- Pre-commit
- Python 3.13.2+

This project uses [poetry](https://python-poetry.org/) for dependency management. Linting and type checking are accomplished using
[pre-commit](https://pre-commit.com/) which is installed by poetry.

## Developer Setup

1. Install [poetry](https://python-poetry.org/).
2. In the project root run `poetry install --with dev` to install dependencies.
3. Run `poetry run pre-commit install` to install pre-commit hooks.
4. Optionally use `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks` or `poetry run pre-commit run --all-files` from the terminal to
   manually run pre-commit hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed or fail tests will be unstaged. After
reviewing these changes or making corrections, you can re-stage the changes and recommit or rerun the checks. After the pre-commit hook run succeeds, your
commit can proceed.

## VS Code

See the .vscode/settings.json.example file for starter settings
