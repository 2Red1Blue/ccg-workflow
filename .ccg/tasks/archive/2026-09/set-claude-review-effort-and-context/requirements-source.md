# Set Claude review effort low and narrow analysis context

Change the durable CCG review supervisor so `review` defaults the Claude leaf
to the lowest supported effort, `low`. Preserve the explicit
`--claude-effort` override and the environment override for review runs.
Keep Codex's existing model and effort behavior unchanged.

Keep analysis inputs explicit and bounded: the next analysis must pass only the
requirements plus the supervisor implementation, its focused tests, and the
smallest relevant documentation. Do not broaden the supervisor's input cap or
feed whole repository instruction files, generated output, or unrelated source.

Update source tests/docs as needed, install the corrected supervisor, and verify
the parsed default and a small receipt records `claude_review_effort: low`.
