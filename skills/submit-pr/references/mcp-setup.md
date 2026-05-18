# MCP Configuration Guide for PR Submission

## GitHub MCP Server

### For Kiro IDE

File: `.kiro/settings/mcp.json` (workspace) or `~/.kiro/settings/mcp.json` (global)

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token>"
      }
    }
  }
}
```

### For Claude Code

File: `~/.claude/settings/mcp.json`

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "<your-token>"
      }
    }
  }
}
```

### Token Generation

1. Visit: https://github.com/settings/tokens/new
2. Note: "openvela PR submission"
3. Expiration: 90 days (recommended)
4. Scopes: check `repo` (Full control of private repositories)
5. Click "Generate token"
6. Copy the token (starts with `ghp_`)

### Verify

After configuration, restart IDE and test:
- Kiro: Cmd+Shift+P → "MCP: Reconnect Servers"
- Claude Code: restart terminal

## Gitee API (No MCP Server)

Gitee does not have an official MCP server. Use REST API via curl.

### Token Generation

1. Visit: https://gitee.com/profile/personal_access_tokens/new
2. Description: "openvela PR submission"
3. Scopes: check `projects` and `pull_requests`
4. Submit and copy token

### API Usage

Create PR:
```bash
curl -X POST "https://gitee.com/api/v5/repos/{owner}/{repo}/pulls" \
  -H "Content-Type: application/json" \
  -d '{
    "access_token": "<your-token>",
    "title": "PR title",
    "head": "your-branch",
    "base": "dev",
    "body": "PR description"
  }'
```

Fork repo:
```bash
curl -X POST "https://gitee.com/api/v5/repos/{owner}/{repo}/forks" \
  -H "Content-Type: application/json" \
  -d '{"access_token": "<your-token>"}'
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `npx` not found | Install Node.js: `sudo apt install nodejs npm` |
| Token expired | Regenerate at GitHub/Gitee settings |
| 403 Forbidden | Token lacks `repo` scope |
| MCP not connecting | Check JSON syntax, restart IDE |
| Rate limited | Wait 1 hour or use authenticated requests |
