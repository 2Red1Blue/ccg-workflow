package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"syscall"
	"time"
)

const (
	sessionStoreSchemaVersion = 1
	sessionStoreDirName       = "sessions-v1"
	sessionStoreMaxRecords    = 2048
	sessionStoreMaxRecordSize = 16 * 1024
	sessionStoreMaxIDBytes    = 4096
)

type sessionRecord struct {
	Version   int    `json:"version"`
	SessionID string `json:"session_id"`
	Backend   string `json:"backend"`
	CreatedAt string `json:"created_at"`
}

var sessionStateDirFn = defaultSessionStateDir

func defaultSessionStateDir() (string, error) {
	if root := strings.TrimSpace(os.Getenv("CODEAGENT_STATE_DIR")); root != "" {
		return filepath.Join(root, sessionStoreDirName), nil
	}
	configDir, err := os.UserConfigDir()
	if err != nil {
		return "", fmt.Errorf("resolve user config directory: %w", err)
	}
	return filepath.Join(configDir, "codeagent-wrapper", sessionStoreDirName), nil
}

func parseSessionRef(ref string) (backend, sessionID string, qualified bool, err error) {
	ref = strings.TrimSpace(ref)
	if ref == "" {
		return "", "", false, errors.New("session reference is empty")
	}
	if len(ref) > sessionStoreMaxIDBytes {
		return "", "", false, fmt.Errorf("session reference exceeds %d bytes", sessionStoreMaxIDBytes)
	}

	separator := strings.IndexByte(ref, ':')
	if separator < 0 {
		return "", ref, false, nil
	}
	if separator == 0 || separator == len(ref)-1 {
		return "", "", false, fmt.Errorf("invalid qualified session reference %q; expected <backend>:<session-id>", ref)
	}

	selected, selectErr := selectBackend(ref[:separator])
	if selectErr != nil {
		return "", "", false, fmt.Errorf("invalid qualified session reference %q: %w", ref, selectErr)
	}
	return selected.Name(), ref[separator+1:], true, nil
}

func formatSessionRef(backend, sessionID string) string {
	return strings.TrimSpace(backend) + ":" + strings.TrimSpace(sessionID)
}

func resolveResumeSession(ref, requestedBackend string, backendExplicit bool) (backend, sessionID string, err error) {
	qualifiedBackend, rawID, qualified, err := parseSessionRef(ref)
	if err != nil {
		return "", "", err
	}

	var explicitBackend string
	if backendExplicit {
		selected, selectErr := selectBackend(requestedBackend)
		if selectErr != nil {
			return "", "", selectErr
		}
		explicitBackend = selected.Name()
	}
	if qualified && explicitBackend != "" && explicitBackend != qualifiedBackend {
		return "", "", fmt.Errorf("session backend conflict: reference selects %s but --backend selects %s", qualifiedBackend, explicitBackend)
	}

	recordedBackend, found, lookupErr := lookupSessionBackend(rawID)
	if lookupErr != nil {
		if !qualified && explicitBackend == "" {
			return "", "", fmt.Errorf("read session backend binding for %q: %w", rawID, lookupErr)
		}
		logWarn(fmt.Sprintf("Ignoring unreadable session binding for %q because the request selects its backend explicitly: %v", rawID, lookupErr))
		found = false
	}
	for _, candidate := range []string{qualifiedBackend, explicitBackend} {
		if candidate != "" && found && candidate != recordedBackend {
			return "", "", fmt.Errorf("session backend conflict: session %q is recorded for %s but the request selects %s", rawID, recordedBackend, candidate)
		}
	}

	switch {
	case qualified:
		return qualifiedBackend, rawID, nil
	case explicitBackend != "":
		return explicitBackend, rawID, nil
	case found:
		return recordedBackend, rawID, nil
	default:
		return "", "", fmt.Errorf("backend is unknown for legacy session %q; retry with --backend <name> or use <backend>:%s", rawID, rawID)
	}
}

func sessionRecordPath(dir, sessionID string) string {
	digest := sha256.Sum256([]byte(sessionID))
	return filepath.Join(dir, hex.EncodeToString(digest[:])+".json")
}

func lookupSessionBackend(sessionID string) (string, bool, error) {
	dir, err := sessionStateDirFn()
	if err != nil {
		return "", false, err
	}
	data, err := os.ReadFile(sessionRecordPath(dir, sessionID))
	if errors.Is(err, os.ErrNotExist) {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	if len(data) > sessionStoreMaxRecordSize {
		return "", false, fmt.Errorf("session record exceeds %d bytes", sessionStoreMaxRecordSize)
	}

	var record sessionRecord
	if err := json.Unmarshal(data, &record); err != nil {
		return "", false, fmt.Errorf("decode session record: %w", err)
	}
	if record.Version != sessionStoreSchemaVersion || record.SessionID != sessionID {
		return "", false, errors.New("session record identity or schema mismatch")
	}
	selected, err := selectBackend(record.Backend)
	if err != nil {
		return "", false, fmt.Errorf("invalid recorded backend: %w", err)
	}
	return selected.Name(), true, nil
}

func registerSessionBackend(sessionID, backend string) error {
	sessionID = strings.TrimSpace(sessionID)
	if sessionID == "" || len(sessionID) > sessionStoreMaxIDBytes {
		return errors.New("cannot register empty or oversized session ID")
	}
	selected, err := selectBackend(backend)
	if err != nil {
		return err
	}
	backend = selected.Name()

	if recorded, found, lookupErr := lookupSessionBackend(sessionID); lookupErr != nil {
		return lookupErr
	} else if found {
		if recorded != backend {
			return fmt.Errorf("session %q is already bound to %s, cannot bind it to %s", sessionID, recorded, backend)
		}
		return nil
	}

	dir, err := sessionStateDirFn()
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return fmt.Errorf("create session store: %w", err)
	}
	if err := os.Chmod(dir, 0o700); err != nil && !isWindows() {
		return fmt.Errorf("secure session store directory: %w", err)
	}

	record := sessionRecord{
		Version:   sessionStoreSchemaVersion,
		SessionID: sessionID,
		Backend:   backend,
		CreatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}
	data, err := json.Marshal(record)
	if err != nil {
		return err
	}
	data = append(data, '\n')

	tmp, err := os.CreateTemp(dir, ".session-*.tmp")
	if err != nil {
		return fmt.Errorf("create temporary session record: %w", err)
	}
	tmpPath := tmp.Name()
	defer func() { _ = os.Remove(tmpPath) }()
	if err := tmp.Chmod(0o600); err != nil && !isWindows() {
		_ = tmp.Close()
		return fmt.Errorf("secure temporary session record: %w", err)
	}
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("write temporary session record: %w", err)
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return fmt.Errorf("sync temporary session record: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return fmt.Errorf("close temporary session record: %w", err)
	}

	target := sessionRecordPath(dir, sessionID)
	if err := os.Link(tmpPath, target); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return fmt.Errorf("publish session record: %w", err)
		}
		recorded, found, lookupErr := lookupSessionBackend(sessionID)
		if lookupErr != nil {
			return lookupErr
		}
		if !found || recorded != backend {
			return fmt.Errorf("concurrent session binding conflict for %q", sessionID)
		}
		return nil
	}
	if err := syncDirectory(dir); err != nil {
		return fmt.Errorf("sync session store directory: %w", err)
	}
	cleanupSessionStore(dir)
	return nil
}

func syncDirectory(dir string) error {
	if isWindows() {
		return nil
	}
	f, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer f.Close()
	return normalizeDirectorySyncError(runtime.GOOS, f.Sync())
}

func normalizeDirectorySyncError(goos string, err error) error {
	if err == nil {
		return nil
	}
	if goos == "darwin" && errors.Is(err, syscall.EINVAL) {
		// Some Darwin filesystems reject directory fsync even though the link
		// publication itself succeeded. Preserve availability on those mounts.
		return nil
	}
	return err
}

func cleanupSessionStore(dir string) {
	release, acquired := acquireSessionCleanupLock(dir)
	if !acquired {
		return
	}
	defer release()

	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	type recordFile struct {
		path    string
		modTime time.Time
	}
	var records []recordFile
	for _, entry := range entries {
		name := entry.Name()
		path := filepath.Join(dir, name)
		if strings.HasPrefix(name, ".session-") && strings.HasSuffix(name, ".tmp") {
			if info, infoErr := entry.Info(); infoErr == nil && time.Since(info.ModTime()) > 24*time.Hour {
				_ = os.Remove(path)
			}
			continue
		}
		if entry.Type().IsRegular() && strings.HasSuffix(name, ".json") {
			if info, infoErr := entry.Info(); infoErr == nil {
				records = append(records, recordFile{path: path, modTime: info.ModTime()})
			}
		}
	}
	if len(records) <= sessionStoreMaxRecords {
		return
	}
	sort.Slice(records, func(i, j int) bool { return records[i].modTime.Before(records[j].modTime) })
	for _, record := range records[:len(records)-sessionStoreMaxRecords] {
		_ = os.Remove(record.path)
	}
}

func acquireSessionCleanupLock(dir string) (func(), bool) {
	lockPath := filepath.Join(dir, ".cleanup.lock")
	for attempt := 0; attempt < 2; attempt++ {
		lock, err := os.OpenFile(lockPath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600)
		if err == nil {
			_ = lock.Close()
			return func() { _ = os.Remove(lockPath) }, true
		}
		if !errors.Is(err, os.ErrExist) {
			return func() {}, false
		}
		info, statErr := os.Stat(lockPath)
		if statErr != nil || time.Since(info.ModTime()) <= 10*time.Minute {
			return func() {}, false
		}
		if removeErr := os.Remove(lockPath); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
			return func() {}, false
		}
	}
	return func() {}, false
}
