# Publishing

This repository is intended to be published only after a sanitized-history pass.

## Public Release Checklist

Before the first public push:

1. Run the local prepublish gate.
2. Confirm `README.md`, `LICENSE`, `docs/ARCHITECTURE.md`, and `docs/CONFIGURATION.md` are accurate.
3. Confirm the deterministic test suite passes.
4. Confirm the autostart service is running from `~/.local/share/phonebridge/runtime/current`.
5. Manually verify notifications, incoming call popup behavior, Files flow, and the Hyprland toggle on the real machine.

## Safe History Rewrite Workflow

Do not rewrite your only local branch without a backup.

Recommended flow:

```bash
git branch private-history-backup
git tag private-history-backup-$(date +%Y%m%d)
```

Then create a clean public branch with one of these approaches:

### Option A: Fresh public branch

```bash
git switch --orphan public-main
git reset
git add .
git commit -m "Public OSS v1"
```

### Option B: Filtered history

If you want to preserve some history, use `git filter-repo` after taking a backup and removing private-only files from every commit.

## What Must Not Be Public

- absolute machine-specific home paths
- private audit evidence
- personal workstation service files
- generated runtime artifacts
- secrets, tokens, or real infrastructure identifiers

The repository-level prepublish gate checks the current tree. History still needs a conscious rewrite step before publication.
