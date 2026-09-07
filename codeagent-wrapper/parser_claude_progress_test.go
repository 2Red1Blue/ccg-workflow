package main

import (
	"strings"
	"testing"
	"time"
)

func TestClaudeProgressIsLiveRedactedAndCompletes(t *testing.T) {
	stream := strings.Join([]string{
		`{"type":"system","subtype":"init","session_id":"claude-session"}`,
		`{"type":"system","subtype":"thinking_tokens","estimated_tokens":10,"session_id":"claude-session"}`,
		`{"type":"system","subtype":"thinking_tokens","estimated_tokens":11,"session_id":"claude-session"}`,
		`{"type":"assistant","session_id":"claude-session","message":{"role":"assistant","content":[{"type":"thinking","thinking":"SECRET_THINKING"},{"type":"tool_use","name":"mcp__safe-tool","input":{"path":"/secret/path","token":"SECRET_TOKEN"}},{"type":"text","text":"SECRET_DRAFT"}]}}`,
		`{"type":"user","session_id":"claude-session","message":{"role":"user","content":[{"type":"tool_result","is_error":false,"content":"SECRET_TOOL_OUTPUT"}]}}`,
		`{"type":"result","subtype":"success","session_id":"claude-session","result":"final answer"}`,
	}, "\n") + "\n"

	var progress []string
	message, sessionID := parseJSONStreamInternalWithContent(strings.NewReader(stream), nil, nil, nil, nil, nil, func(line string) {
		progress = append(progress, line)
	}, nil)
	joined := strings.Join(progress, "\n")
	for _, expected := range []string{"session_started", "claude_thinking", "claude_tool_started", "claude_assistant_message", "claude_tool_completed", "session_completed"} {
		if !strings.Contains(joined, expected) {
			t.Fatalf("progress missing %q: %s", expected, joined)
		}
	}
	for _, secret := range []string{"SECRET_THINKING", "SECRET_TOKEN", "/secret/path", "SECRET_DRAFT", "SECRET_TOOL_OUTPUT"} {
		if strings.Contains(joined, secret) {
			t.Fatalf("progress leaked %q: %s", secret, joined)
		}
	}
	if strings.Count(joined, "claude_thinking") != 1 {
		t.Fatalf("thinking progress was not throttled: %s", joined)
	}
	if message != "final answer" || sessionID != "claude-session" {
		t.Fatalf("got message=%q session=%q", message, sessionID)
	}
}

func TestClaudeNestedSessionIDIsCaptured(t *testing.T) {
	stream := `{"type":"assistant","message":{"role":"assistant","session_id":"nested-session","content":[{"type":"text","text":"draft"}]}}` + "\n" +
		`{"type":"result","subtype":"success","session_id":"nested-session","result":"done"}` + "\n"
	message, sessionID := parseJSONStream(strings.NewReader(stream))
	if message != "done" || sessionID != "nested-session" {
		t.Fatalf("got message=%q session=%q", message, sessionID)
	}
}

func TestClaudePlainStringUserContentIsAcceptedWithoutWarnings(t *testing.T) {
	stream := `{"type":"user","session_id":"claude-session","message":{"role":"user","content":"plain echoed prompt"}}` + "\n" +
		`{"type":"result","subtype":"success","session_id":"claude-session","result":"done"}` + "\n"
	var warnings []string
	message, sessionID := parseJSONStreamInternalWithContent(strings.NewReader(stream), func(line string) {
		warnings = append(warnings, line)
	}, nil, nil, nil, nil, nil, nil)
	if len(warnings) != 0 {
		t.Fatalf("unexpected warnings: %#v", warnings)
	}
	if message != "done" || sessionID != "claude-session" {
		t.Fatalf("got message=%q session=%q", message, sessionID)
	}
}

func TestNonClaudeAssistantMessageShapeIsNotMisclassified(t *testing.T) {
	stream := `{"type":"assistant","session_id":"other-session","message":{"role":"different-backend","content":[{"type":"text"}]}}` + "\n"
	message, sessionID := parseJSONStream(strings.NewReader(stream))
	if message != "" || sessionID != "other-session" {
		t.Fatalf("got message=%q session=%q", message, sessionID)
	}
}

func TestClaudeProgressEmitterThrottleAndCap(t *testing.T) {
	now := time.Unix(100, 0)
	var lines []string
	emitter := newClaudeProgressEmitter(func(line string) { lines = append(lines, line) }, func() time.Time { return now })
	emitter.emitThrottled("thinking", "first")
	emitter.emitThrottled("thinking", "suppressed")
	now = now.Add(claudeProgressMinInterval)
	emitter.emitThrottled("thinking", "second")
	if strings.Join(lines, ",") != "first,second" {
		t.Fatalf("lines = %#v", lines)
	}
	emitter.emitCount = claudeProgressMaxEvents
	now = now.Add(claudeProgressMinInterval)
	emitter.emitThrottled("thinking", "capped")
	if len(lines) != 2 {
		t.Fatalf("cap failed: %#v", lines)
	}
}

func TestSafeProgressIdentifier(t *testing.T) {
	if got := safeProgressIdentifier("mcp tool:/secret", 64); got != "mcp_tool__secret" {
		t.Fatalf("got %q", got)
	}
}
