#!/bin/bash

# Make `../../span/span-panel-api` mean the library checkout from here.
#
# `[tool.uv.sources]` names the checkout by a path relative to the repository
# root, so that one spelling means the same directory on every machine. It does,
# from the primary checkout and from a worktree placed beside it -- but a
# worktree nested *inside* the checkout, as `.claude/worktrees/<name>` is, sits
# two levels deeper, and the same relative path lands on a directory that does
# not exist. That is not a soft failure: `uv run` cannot build an environment at
# all, so every hook that runs through it -- pylint, mypy, vulture, both radon
# hooks and the test suite -- fails before it starts, and the first commit from a
# new worktree is where you find out.
#
# So bridge it: a symlink at the place the nested layout looks, pointing at the
# directory the primary checkout means. Both ends are derived from git rather
# than written down here, so this holds under any worktree layout and on any
# machine. It is a link and not an edit to `pyproject.toml` for the reason
# `scripts/check-library-path.py` exists: that file is shared, and a path in it
# redirects every import in the suite for everyone.
bridge_library_checkout() {
    local git_common_dir checkout_root library_root worktree_root bridge

    git_common_dir=$(git rev-parse --git-common-dir 2>/dev/null) || return 0
    if [[ $git_common_dir != /* ]]; then
        git_common_dir="$PWD/$git_common_dir"
    fi
    checkout_root=$(cd "$(dirname "$git_common_dir")" && pwd) || return 0

    worktree_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 0
    bridge="$worktree_root/../../span"

    # True in the primary checkout, where the relative path already arrives, and
    # in a worktree that has been bridged already. Both want nothing done.
    if [[ -e $bridge ]]; then
        return 0
    fi

    library_root=$(cd "$checkout_root/../../span" 2>/dev/null && pwd)
    if [[ -z $library_root ]]; then
        echo "No library checkout beside $checkout_root; skipping the worktree bridge."
        return 0
    fi

    ln -s "$library_root" "$bridge" || return 1
    echo "Bridged $library_root into this worktree so ../../span resolves."
}

bridge_library_checkout
if [[ $? -ne 0 ]]; then
    echo "Failed to bridge the library checkout. Please check the output above."
    exit 1
fi

# Ensure dependencies are up to date (uv sync is fast and idempotent)
uv sync
if [[ $? -ne 0 ]]; then
    echo "Failed to install dependencies. Please check the output above."
    exit 1
fi

# Install pre-commit hooks (only if not already installed). Asked of git rather
# than assumed to be `.git/hooks`, because a worktree's `.git` is a file and its
# hooks live in the shared common directory.
hooks_path=$(git rev-parse --git-path hooks/pre-commit)
if [[ ! -f "$hooks_path" ]]; then
    prek install
    echo "Git hooks installed successfully!"
fi
