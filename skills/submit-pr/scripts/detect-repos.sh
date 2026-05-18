#!/bin/bash
# detect-repos.sh - Detect modified repositories and platform info in openvela workspace
# Usage: bash detect-repos.sh [repo_root]
#
# Output: JSON-like summary of modified repos, platform, and branch info

REPO_ROOT="${1:-.}"

echo "=== openvela Repository Status ==="
echo ""

# Detect platform from first available repo
detect_platform() {
    local remote_url
    # Try direct git remote first
    remote_url=$(cd "$REPO_ROOT/nuttx" 2>/dev/null && git remote get-url origin 2>/dev/null)
    # Fallback: check .repo/manifests remote
    if [ -z "$remote_url" ] && [ -d "$REPO_ROOT/.repo/manifests" ]; then
        remote_url=$(git -C "$REPO_ROOT/.repo/manifests" remote get-url origin 2>/dev/null)
    fi
    # Fallback: check manifest.xml
    if [ -z "$remote_url" ] && [ -f "$REPO_ROOT/.repo/manifest.xml" ]; then
        remote_url=$(grep -o 'fetch="[^"]*"' "$REPO_ROOT/.repo/manifest.xml" | head -1 | tr -d '"' | sed 's/fetch=//')
    fi

    if echo "$remote_url" | grep -q "github.com"; then
        echo "PLATFORM=github"
        echo "ORG=open-vela"
    elif echo "$remote_url" | grep -q "gitee.com"; then
        echo "PLATFORM=gitee"
        echo "ORG=open-vela"
    else
        echo "PLATFORM=unknown"
        echo "REMOTE_URL=$remote_url"
    fi
}

# Detect current branch
detect_branch() {
    local branch
    branch=$(cd "$REPO_ROOT/nuttx" 2>/dev/null && git branch --show-current 2>/dev/null)
    if [ -z "$branch" ]; then
        branch=$(cd "$REPO_ROOT/nuttx" 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null)
    fi
    echo "CURRENT_BRANCH=$branch"
}

# Find all modified repos
find_modified_repos() {
    echo ""
    echo "=== Modified Repositories ==="

    if [ -f "$REPO_ROOT/.repo/project.list" ]; then
        # Use repo tool if available
        while IFS= read -r project; do
            local project_path="$REPO_ROOT/$project"
            if [ -d "$project_path/.git" ] || [ -f "$project_path/.git" ]; then
                local status
                status=$(cd "$project_path" && git status --porcelain 2>/dev/null)
                if [ -n "$status" ]; then
                    echo ""
                    echo "REPO=$project"
                    echo "PATH=$project_path"
                    echo "FILES:"
                    echo "$status" | while IFS= read -r line; do
                        echo "  $line"
                    done
                fi
            fi
        done < "$REPO_ROOT/.repo/project.list"
    else
        # Fallback: check common directories
        for dir in nuttx apps vendor frameworks packages external; do
            if [ -d "$REPO_ROOT/$dir" ]; then
                local status
                status=$(cd "$REPO_ROOT/$dir" && git status --porcelain 2>/dev/null)
                if [ -n "$status" ]; then
                    echo ""
                    echo "REPO=$dir"
                    echo "PATH=$REPO_ROOT/$dir"
                    echo "FILES:"
                    echo "$status" | while IFS= read -r line; do
                        echo "  $line"
                    done
                fi
            fi
        done
    fi
}

# Check MCP availability
check_mcp() {
    echo ""
    echo "=== MCP Status ==="
    if [ -f "$HOME/.kiro/settings/mcp.json" ]; then
        echo "KIRO_MCP=found ($HOME/.kiro/settings/mcp.json)"
    elif [ -f "$REPO_ROOT/.kiro/settings/mcp.json" ]; then
        echo "KIRO_MCP=found ($REPO_ROOT/.kiro/settings/mcp.json)"
    else
        echo "KIRO_MCP=not_found"
    fi

    if [ -f "$HOME/.claude/settings/mcp.json" ]; then
        echo "CLAUDE_MCP=found ($HOME/.claude/settings/mcp.json)"
    else
        echo "CLAUDE_MCP=not_found"
    fi
}

# Check git user config
check_git_user() {
    echo ""
    echo "=== Git User ==="
    echo "USER_NAME=$(git config user.name 2>/dev/null)"
    echo "USER_EMAIL=$(git config user.email 2>/dev/null)"
}

# Run all checks
detect_platform
detect_branch
check_git_user
check_mcp
find_modified_repos

echo ""
echo "=== Done ==="
