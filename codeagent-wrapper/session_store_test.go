package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
)

func TestMain(m *testing.M) {
	// Test WebServer instances must never create real tabs via the companion.
	_ = os.Setenv("CODEAGENT_WEB_UI_AUTO_OPEN", "false")
	root, err := os.MkdirTemp("", "codeagent-wrapper-session-tests-")
	if err != nil {
		panic(err)
	}
	sessionStateDirFn = func() (string, error) {
		return filepath.Join(root, sessionStoreDirName), nil
	}
	code := m.Run()
	_ = os.RemoveAll(root)
	os.Exit(code)
}

func useTemporarySessionStore(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	previous := sessionStateDirFn
	sessionStateDirFn = func() (string, error) {
		return filepath.Join(root, sessionStoreDirName), nil
	}
	t.Cleanup(func() { sessionStateDirFn = previous })
	return root
}

func TestParseSessionRef(t *testing.T) {
	tests := []struct {
		name        string
		ref         string
		backend     string
		sessionID   string
		qualified   bool
		wantErrPart string
	}{
		{name: "legacy raw", ref: "session-1", sessionID: "session-1"},
		{name: "qualified Claude", ref: "claude:session-1", backend: "claude", sessionID: "session-1", qualified: true},
		{name: "canonical alias", ref: "agy:session-1", backend: "antigravity", sessionID: "session-1", qualified: true},
		{name: "unknown backend", ref: "unknown:session-1", wantErrPart: "unsupported backend"},
		{name: "empty ID", ref: "claude:", wantErrPart: "invalid qualified"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			backend, sessionID, qualified, err := parseSessionRef(test.ref)
			if test.wantErrPart != "" {
				if err == nil || !strings.Contains(err.Error(), test.wantErrPart) {
					t.Fatalf("error = %v, want containing %q", err, test.wantErrPart)
				}
				return
			}
			if err != nil || backend != test.backend || sessionID != test.sessionID || qualified != test.qualified {
				t.Fatalf("got backend=%q id=%q qualified=%v err=%v", backend, sessionID, qualified, err)
			}
		})
	}
}

func TestResolveResumeSessionPrecedenceAndConflicts(t *testing.T) {
	useTemporarySessionStore(t)
	if err := registerSessionBackend("known", "claude"); err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name        string
		ref         string
		requested   string
		explicit    bool
		wantBackend string
		wantID      string
		wantErrPart string
	}{
		{name: "recorded raw", ref: "known", wantBackend: "claude", wantID: "known"},
		{name: "qualified portable", ref: "claude:portable", wantBackend: "claude", wantID: "portable"},
		{name: "legacy explicit", ref: "legacy", requested: "claude", explicit: true, wantBackend: "claude", wantID: "legacy"},
		{name: "unknown fail closed", ref: "legacy", wantErrPart: "backend is unknown"},
		{name: "explicit versus record", ref: "known", requested: "codex", explicit: true, wantErrPart: "conflict"},
		{name: "qualified versus explicit", ref: "claude:portable", requested: "codex", explicit: true, wantErrPart: "conflict"},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			backend, sessionID, err := resolveResumeSession(test.ref, test.requested, test.explicit)
			if test.wantErrPart != "" {
				if err == nil || !strings.Contains(err.Error(), test.wantErrPart) {
					t.Fatalf("error = %v, want containing %q", err, test.wantErrPart)
				}
				return
			}
			if err != nil || backend != test.wantBackend || sessionID != test.wantID {
				t.Fatalf("got backend=%q id=%q err=%v", backend, sessionID, err)
			}
		})
	}
}

func TestSessionStoreRoundTripAndConflict(t *testing.T) {
	root := useTemporarySessionStore(t)
	if err := registerSessionBackend("session/with unsafe filename bytes", "claude"); err != nil {
		t.Fatal(err)
	}
	backend, found, err := lookupSessionBackend("session/with unsafe filename bytes")
	if err != nil || !found || backend != "claude" {
		t.Fatalf("lookup got backend=%q found=%v err=%v", backend, found, err)
	}
	if err := registerSessionBackend("session/with unsafe filename bytes", "codex"); err == nil || !strings.Contains(err.Error(), "already bound") {
		t.Fatalf("conflicting registration error = %v", err)
	}

	entries, err := os.ReadDir(filepath.Join(root, sessionStoreDirName))
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || !strings.HasSuffix(entries[0].Name(), ".json") || strings.Contains(entries[0].Name(), "unsafe") {
		t.Fatalf("unexpected store entries: %#v", entries)
	}
}

func TestSessionStoreConcurrentDistinctWrites(t *testing.T) {
	useTemporarySessionStore(t)
	const count = 32
	var wait sync.WaitGroup
	errorsByIndex := make([]error, count)
	for i := 0; i < count; i++ {
		wait.Add(1)
		go func(index int) {
			defer wait.Done()
			errorsByIndex[index] = registerSessionBackend(fmt.Sprintf("session-%d", index), "claude")
		}(i)
	}
	wait.Wait()
	for i, err := range errorsByIndex {
		if err != nil {
			t.Fatalf("register session-%d: %v", i, err)
		}
		backend, found, lookupErr := lookupSessionBackend(fmt.Sprintf("session-%d", i))
		if lookupErr != nil || !found || backend != "claude" {
			t.Fatalf("lookup session-%d got backend=%q found=%v err=%v", i, backend, found, lookupErr)
		}
	}
}

func TestSessionStoreCorruptionFailsClosed(t *testing.T) {
	root := useTemporarySessionStore(t)
	dir := filepath.Join(root, sessionStoreDirName)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	path := sessionRecordPath(dir, "corrupt")
	if err := os.WriteFile(path, []byte("not-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, _, err := resolveResumeSession("corrupt", "", false)
	if err == nil || !strings.Contains(err.Error(), "decode session record") {
		t.Fatalf("error = %v", err)
	}
}

func TestCorruptRecordDoesNotBlockQualifiedOrExplicitResume(t *testing.T) {
	root := useTemporarySessionStore(t)
	dir := filepath.Join(root, sessionStoreDirName)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sessionRecordPath(dir, "corrupt"), []byte("not-json"), 0o600); err != nil {
		t.Fatal(err)
	}

	for _, test := range []struct {
		ref       string
		requested string
		explicit  bool
	}{
		{ref: "claude:corrupt"},
		{ref: "corrupt", requested: "claude", explicit: true},
	} {
		backend, sessionID, err := resolveResumeSession(test.ref, test.requested, test.explicit)
		if err != nil || backend != "claude" || sessionID != "corrupt" {
			t.Fatalf("resolve %q got backend=%q id=%q err=%v", test.ref, backend, sessionID, err)
		}
	}
}

func TestQualifiedReferenceConflictsWithRecordedBackend(t *testing.T) {
	useTemporarySessionStore(t)
	if err := registerSessionBackend("known", "codex"); err != nil {
		t.Fatal(err)
	}
	_, _, err := resolveResumeSession("claude:known", "", false)
	if err == nil || !strings.Contains(err.Error(), "conflict") {
		t.Fatalf("error = %v", err)
	}
}

func TestSessionCleanupLockIsExclusiveAndRecoverable(t *testing.T) {
	root := useTemporarySessionStore(t)
	dir := filepath.Join(root, sessionStoreDirName)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	releaseFirst, acquired := acquireSessionCleanupLock(dir)
	if !acquired {
		t.Fatal("first cleanup lock was not acquired")
	}
	if _, acquired := acquireSessionCleanupLock(dir); acquired {
		t.Fatal("second cleanup lock must not be acquired")
	}
	releaseFirst()
	releaseSecond, acquired := acquireSessionCleanupLock(dir)
	if !acquired {
		t.Fatal("cleanup lock was not recoverable after release")
	}
	releaseSecond()
}

func TestSessionBindingTrackerPersistsBeforeCallerOutcome(t *testing.T) {
	useTemporarySessionStore(t)
	tracker := &sessionBindingTracker{}
	tracker.start("early-session", "claude")
	if err := tracker.wait(); err != nil {
		t.Fatal(err)
	}
	backend, found, err := lookupSessionBackend("early-session")
	if err != nil || !found || backend != "claude" {
		t.Fatalf("lookup got backend=%q found=%v err=%v", backend, found, err)
	}
}

func TestParallelOutputSurfacesSessionBindingWarnings(t *testing.T) {
	result := TaskResult{TaskID: "review", ExitCode: 0, Message: "done", Warnings: []string{"binding unavailable"}}
	for _, summaryOnly := range []bool{true, false} {
		output := generateFinalOutputWithMode([]TaskResult{result}, summaryOnly)
		if !strings.Contains(output, "Warning: binding unavailable") {
			t.Fatalf("summaryOnly=%v output=%q", summaryOnly, output)
		}
	}
}

func TestFailedBackendStillPersistsEarlySessionBinding(t *testing.T) {
	defer resetTestHooks()
	useTemporarySessionStore(t)
	fake := newFakeCmd(fakeCmdConfig{
		StdoutPlan: []fakeStdoutEvent{{Data: `{"type":"thread.started","thread_id":"failed-session"}` + "\n"}},
		WaitErr:    errors.New("backend failed"),
	})
	newCommandRunner = func(context.Context, string, ...string) commandRunner { return fake }

	result := runCodexTaskWithContext(context.Background(), TaskSpec{Task: "test", WorkDir: defaultWorkdir}, CodexBackend{}, nil, false, true, 10)
	if result.ExitCode == 0 {
		t.Fatalf("expected backend failure, got %+v", result)
	}
	backend, found, err := lookupSessionBackend("failed-session")
	if err != nil || !found || backend != "codex" {
		t.Fatalf("lookup got backend=%q found=%v err=%v", backend, found, err)
	}
}

func TestNormalizeDirectorySyncError(t *testing.T) {
	if err := normalizeDirectorySyncError("darwin", syscall.EINVAL); err != nil {
		t.Fatalf("Darwin EINVAL should be treated as unsupported directory fsync: %v", err)
	}
	want := errors.New("disk failure")
	if got := normalizeDirectorySyncError("darwin", want); !errors.Is(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	if got := normalizeDirectorySyncError("linux", syscall.EINVAL); !errors.Is(got, syscall.EINVAL) {
		t.Fatalf("Linux EINVAL must propagate, got %v", got)
	}
}
