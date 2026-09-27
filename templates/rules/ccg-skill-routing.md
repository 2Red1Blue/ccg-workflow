# CCG Domain Skills — On-Demand Use

CCG domain files are reference material, not automatic prompt injections. A keyword or topic mention alone must not trigger reading or including a domain file.

Load a domain reference only when the user explicitly asks for that skill or the selected task workflow identifies it as necessary. Prefer the corresponding installed CCG skill or slash command when available. Otherwise read only the named file and only the sections needed for the current task; do not load an entire domain directory.

If the relevant domain is unclear, use the normal Skill discovery workflow instead of guessing from keywords. Do not repeat the same reference in later turns unless the task needs it again.

Domain references are installed under `~/.claude/skills/ccg/domains/`. Some optional domains may be absent. In particular, security references are not installed by default because their red-team content may trigger antivirus false positives; do not assume they exist or copy them automatically.
