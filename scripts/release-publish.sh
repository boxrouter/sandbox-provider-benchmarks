#!/usr/bin/env bash
# SPDX-FileCopyrightText: BoxRouter, Inc.
# SPDX-License-Identifier: Apache-2.0
# The git steps of release-on-merge, run in a checkout of main whose version is already selected.
#   release-publish.sh prepare <version>  stage and commit the version bump, unless the version is already prepared
#   release-publish.sh publish <tag>      push main only if its commit is not already there, then tag and push the tag
#   release-publish.sh dispatch <tag>     fire release.yml for the tag, retrying until the REST ref index sees it
# A version that an earlier run prepared but could not tag (its tag push timed out) is prepared already: the bump
# changes nothing and main already holds it, so the run only tags. A network call is retried.
set -euo pipefail

retry() {
  local attempt=1
  until "$@"; do
    if [ "$attempt" -ge 3 ]; then return 1; fi
    echo "retry $attempt/3 failed: $*" >&2
    sleep "${RELEASE_RETRY_SLEEP:-10}"
    attempt=$((attempt + 1))
  done
}

case "${1:-}" in
  prepare)
    version="${2:?usage: release-publish.sh prepare <version>}"
    git add pyproject.toml uv.lock
    git diff --cached --check
    if git diff --cached --quiet; then
      echo "Version $version is already prepared; nothing to commit."
    else
      git commit -m "Prepare methodology v$version"
    fi
    ;;
  publish)
    tag="${2:?usage: release-publish.sh publish <tag>}"
    git fetch origin --tags
    if git show-ref --verify --quiet "refs/tags/$tag"; then
      echo "::error::Release tag $tag appeared before publication."
      exit 1
    fi
    if git merge-base --is-ancestor HEAD origin/main; then
      echo "main already holds $(git rev-parse --short HEAD); pushing only the tag."
    else
      retry git push origin HEAD:refs/heads/main
    fi
    git tag -a "$tag" -m "Release $tag" HEAD
    retry git push origin "refs/tags/$tag"
    ;;
  dispatch)
    tag="${2:?usage: release-publish.sh dispatch <tag>}"
    # git accepts the tag push before the REST ref index resolves it; dispatching
    # immediately returns "No ref found", so the dispatch itself is retried.
    retry gh workflow run release.yml --ref "$tag"
    ;;
  *)
    echo "usage: release-publish.sh prepare <version> | publish <tag> | dispatch <tag>" >&2
    exit 2
    ;;
esac
