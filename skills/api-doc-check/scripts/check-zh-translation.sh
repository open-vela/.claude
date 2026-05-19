#!/bin/bash
# Check API docs for translation quality issues.
# - Chinese docs (docs/zh-cn/): detects mixed Chinese-English anti-patterns
# - English docs (docs/en/): detects leaked Chinese characters in prose
#
# Can be used standalone or chained into pre-commit alongside check-api-doc-sync.sh.
#
# Usage:
#   bash scripts/check-zh-translation.sh              # Check staged .md files (default)
#   bash scripts/check-zh-translation.sh file1.md ... # Check specific files

set -euo pipefail

RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Patterns that indicate broken/incomplete translation.
# These match common anti-patterns found in LLM-generated docs.
# We only check lines OUTSIDE of code blocks (```...```).
PATTERNS=(
    # English phrases that should never appear in Chinese prose
    'on success'
    'on failure'
    'on error'
    'from begining'
    'from beginning'
    'the resource'
    'the player'
    'the recorder'
    'the async'
    'the capturing'
    'the playing'
    'the recording'
    'Play or resume'
    'Listen to'
    'Callback to'
    'Subscribe '
    'Unsubscribe '
    'Unubscribe '
    # Garbled partial translations
    $'re\xe9\x9f\xb3\xe9\xa2\x91\xe6\xba\x90'
    'Gert '
    'Rquest'
    # Mixed patterns: Chinese verb + English object
    ' to msec '
    ' loop times'
    ' playing status'
    'for `on_'
    'for `cb'
    # "of" between Chinese words (e.g. "volume of a stream")
    ' of a '
    ' of the '
    ' of re'
    # Common half-translated return value patterns
    'NULL on failure'
    'on playing'
    'on in'$'\xe6\xb4\xbb\xe8\xb7\x83'
    # English verbs that should be translated
    'Increase '
    'Decrease '
    'Query '
    'Update '
    'Force use'
)

# Determine which files to check
files_to_check=()

if [[ $# -gt 0 ]]; then
    files_to_check=("$@")
else
    # Default: check staged API doc files (both zh-cn and en)
    while IFS= read -r f; do
        if [[ "$f" == docs/zh-cn/api/*.md ]] || [[ "$f" == docs/en/api/*.md ]]; then
            files_to_check+=("$f")
        fi
    done < <(git diff --cached --name-only 2>/dev/null || true)
fi

if [[ ${#files_to_check[@]} -eq 0 ]]; then
    exit 0
fi

issues=0

for file in "${files_to_check[@]}"; do
    [[ -f "$file" ]] || continue

    # Determine check mode based on file path
    if [[ "$file" == *docs/en/* ]]; then
        # === English doc: check for leaked Chinese characters ===
        in_code_block=0
        line_num=0
        file_issues=0

        while IFS= read -r line; do
            line_num=$((line_num + 1))

            # Toggle code block state
            if [[ "$line" =~ ^\`\`\` ]]; then
                if [[ $in_code_block -eq 0 ]]; then
                    in_code_block=1
                else
                    in_code_block=0
                fi
                continue
            fi

            [[ $in_code_block -eq 1 ]] && continue

            # Skip navigation bar (contains CJK nav links)
            [[ "$line" =~ ^\\\[ ]] && continue

            # Check for Chinese characters (Unicode range \u4e00-\u9fff)
            if echo "$line" | grep -Pq '[\x{4e00}-\x{9fff}]'; then
                if [[ $file_issues -eq 0 ]]; then
                    echo -e "${RED}$file${NC}:"
                    file_issues=1
                fi
                echo -e "  ${YELLOW}L${line_num}${NC}: $line"
                echo -e "         ^ Chinese characters found in English doc"
                issues=$((issues + 1))
            fi
        done < "$file"

        [[ $file_issues -gt 0 ]] && echo ""
    else
        # === Chinese doc: check for mixed Chinese-English patterns ===
        in_code_block=0
        line_num=0
        file_issues=0

        while IFS= read -r line; do
            line_num=$((line_num + 1))

            # Toggle code block state
            if [[ "$line" =~ ^\`\`\` ]]; then
                if [[ $in_code_block -eq 0 ]]; then
                    in_code_block=1
                else
                    in_code_block=0
                fi
                continue
            fi

            # Skip lines inside code blocks
            [[ $in_code_block -eq 1 ]] && continue

            # Skip navigation bar and headings with function names
            [[ "$line" =~ ^\\\[ ]] && continue
            [[ "$line" =~ ^### ]] && continue

            # Check each pattern
            for pattern in "${PATTERNS[@]}"; do
                if [[ "$line" == *"$pattern"* ]]; then
                    if [[ $file_issues -eq 0 ]]; then
                        echo -e "${RED}$file${NC}:"
                        file_issues=1
                    fi
                    echo -e "  ${YELLOW}L${line_num}${NC}: $line"
                    echo -e "         ^ matched: \"$pattern\""
                    issues=$((issues + 1))
                    break  # One match per line is enough
                fi
            done
        done < "$file"

        [[ $file_issues -gt 0 ]] && echo ""
    fi
done

if [[ $issues -gt 0 ]]; then
    echo -e "${RED}Found $issues translation quality issue(s).${NC}"
    echo "Please fix before committing, or use 'git commit --no-verify' to skip."
    exit 1
fi

exit 0
