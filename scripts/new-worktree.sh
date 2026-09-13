#!/bin/bash
#
# Create a worktree for a branch of work beside the primary checkout, and prepare
# it with setup-hooks.sh.
#
# Usage: scripts/new-worktree.sh <name> [<branch>]
#
#   <name>    The worktree is created at ../<repository>-<name>, beside the primary
#             checkout. From there `../../span/span-panel-api` still resolves to the
#             library checkout, so no bridge is needed.
#   <branch>  The branch to create. Defaults to feat/<name>.
#
# The branch starts from origin/main as just fetched and does not track it, so a
# bare `git push` can never land on main. Local main is fast-forwarded as well where
# that is safe, and the script refuses when local main holds commits origin/main
# lacks, because branching from origin/main would silently leave them out.
#
# The worktree gets a virtual environment of its own rather than a link to the
# primary checkout's. The environment holds editable installs bound to one checkout's
# sources, so a shared one imports whichever checkout synced last and tests pass
# against the wrong code without an error. uv clones packages from its cache, so the
# second environment costs little. Local configuration that is not bound to a path,
# `.env`, is linked instead.
#
# When a library worktree of the same name exists, the command to test against it is
# printed rather than run: it is a local override that the next `uv sync` silently
# undoes, so it should be a step you take knowingly. See developer.md, "Working
# against unreleased library code".

set -euo pipefail

usage() {
    echo "Usage: $0 <name> [<branch>]" >&2
    exit 2
}

[[ $# -ge 1 && $# -le 2 ]] || usage
name=$1
if [[ ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Worktree name '$name' may use only letters, digits, dots, underscores and hyphens." >&2
    exit 2
fi
branch=${2:-feat/$name}

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
checkout=$(cd "$script_dir/.." && pwd)
common_dir=$(git -C "$checkout" rev-parse --path-format=absolute --git-common-dir)
primary_root=$(dirname "$common_dir")
target="$(dirname "$primary_root")/$(basename "$primary_root")-$name"

if [[ -e "$target" ]]; then
    echo "$target already exists." >&2
    exit 1
fi
if git -C "$primary_root" show-ref --verify --quiet "refs/heads/$branch"; then
    echo "Branch $branch already exists." >&2
    exit 1
fi

echo "Fetching origin..."
git -C "$primary_root" fetch origin

if git -C "$primary_root" show-ref --verify --quiet refs/heads/main; then
    ahead=$(git -C "$primary_root" rev-list --count origin/main..main)
    if ((ahead > 0)); then
        echo "Local main has $ahead commit(s) that origin/main does not. Reconcile main first;" >&2
        echo "branching from origin/main would silently leave them out." >&2
        exit 1
    fi

    # Where main is checked out, move it only if that checkout is clean.
    main_checkout=$(git -C "$primary_root" worktree list --porcelain |
        awk '/^worktree /{wt = substr($0, 10)} /^branch refs\/heads\/main$/{print wt}')
    if [[ -z "$main_checkout" ]]; then
        # update-ref rather than `branch --force`: that would also rewrite main's
        # upstream to origin/main, silently re-pointing a main that tracks another
        # remote. The old value guards against main moving since it was checked.
        git -C "$primary_root" update-ref -m "new-worktree.sh: fast-forward main to origin/main" \
            refs/heads/main "$(git -C "$primary_root" rev-parse refs/remotes/origin/main)" \
            "$(git -C "$primary_root" rev-parse refs/heads/main)"
    elif [[ -n "$(git -C "$main_checkout" status --porcelain --untracked-files=no)" ]]; then
        echo "main is checked out with uncommitted changes at $main_checkout; leaving it as it is."
    elif ! git -C "$main_checkout" merge --ff-only --quiet origin/main; then
        # Untracked files are not counted as changes above, because a fast-forward
        # refuses to overwrite one; this is where that refusal lands.
        echo "Could not fast-forward main at $main_checkout; leaving it as it is."
    fi
fi

echo "Creating $target on $branch from origin/main..."
git -C "$primary_root" worktree add --no-track -b "$branch" "$target" origin/main

if [[ -f "$primary_root/.env" && ! -e "$target/.env" ]]; then
    ln -s "$primary_root/.env" "$target/.env"
    echo "Linked .env from the primary checkout."
fi

echo "Preparing the worktree..."
(cd "$target" && bash ./setup-hooks.sh)

echo
echo "Worktree ready: $target"
echo "Branch:         $branch, from $(git -C "$target" rev-parse --short HEAD), not tracking a remote"

if [[ -d "$target/../../span/span-panel-api-$name" ]]; then
    echo
    echo "A library worktree of the same name exists. To test against it, install all three of"
    echo "its distributions into this worktree's environment; the next \`uv sync\` undoes it:"
    echo "    uv pip install -e ../../span/span-panel-api-$name \\"
    echo "                   -e ../../span/span-panel-api-$name/packages/schema-0 \\"
    echo "                   -e ../../span/span-panel-api-$name/packages/schema-1"
fi
