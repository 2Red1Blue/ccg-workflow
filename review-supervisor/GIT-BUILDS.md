# Source-managed local wrapper

The Go source of session-to-backend bindings, restart-safe `SESSION_REF`,
bounded Claude progress reporting, and Web UI behavior is tracked together in
this fork. Generated binaries and build receipts stay outside Git.

After committing a clean checkout, install its exact version on macOS/Linux:

```sh
python3 scripts/install-local-wrapper.py
~/.claude/bin/codeagent-wrapper --build-info
```

The installer builds with `-trimpath` and embeds `git rev-parse HEAD`. It refuses
dirty inputs or a checkout changed during compilation. It installs:

- `~/.claude/bin/codeagent-wrapper`: the tracked Python recursion/hash guard;
- `~/.claude/bin/codeagent-wrapper.real`: the generated Go executable;
- `~/.claude/bin/codeagent-wrapper.build.json`: Git commit, binary hash, Go
  version and installation timestamp.

The guard reads the receipt's hash instead of embedding a machine-specific hash
in its source. Installation is serialized and restores previous files on
failure; pre-install copies are retained under `~/.claude/backups/wrapper-git-*`.
CCG's npm installer/uninstaller preserves these separately managed builds;
use the source installer to update them. No release binary is uploaded by this
local command. The release workflow runs on main only and embeds its commit too.

The two completion-timing tests explicitly set their grace period to zero.
They verify that an intermediate answer cannot terminate a running backend,
and that a completion event permits cleanup, independently of the production
five-second grace. Production defaults are unchanged.

The original worktree's uncommitted changes are migrated to this branch in a
separate commit. After verification, that worktree can check out the same branch;
its previous dirty state can be retained as a named stash for recovery.
