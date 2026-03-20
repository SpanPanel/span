# Development Notes

## Developer Prerequisites

- uv
- prek
- Python 3.14.2+

This project uses [uv](https://docs.astral.sh/uv/) for dependency management. Linting and type checking are accomplished using
[prek](https://github.com/j178/prek), a fast Rust-based pre-commit framework.

## Developer Setup

1. Install [uv](https://docs.astral.sh/uv/).
2. In the project root run `uv sync` to install dependencies.
3. Run `prek install` to install pre-commit hooks.
4. Optionally use `Tasks: Run Task` from the command palette to run `Run all Pre-commit checks` or `prek run --all-files` from the terminal to manually run
   hooks on files locally in your environment as you make changes.

The linters may make changes to files when you try to commit, for example to sort imports. Files that are changed or fail tests will be unstaged. After
reviewing these changes or making corrections, you can re-stage the changes and recommit or rerun the checks. After the prek hook run succeeds, your commit can
proceed.

## VS Code

See the .vscode/settings.json.example file for starter settings
