package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// A lease advertises only a loopback URL and expires if a wrapper is killed.
// Chrome owns the tab ID; the wrapper never drives focus or other browser tabs.
type browserLease struct {
	path   string
	done   chan struct{}
	exited chan struct{}
	once   sync.Once
}

func publishBrowserLease(port int) (*browserLease, error) {
	root, err := os.UserConfigDir()
	if err != nil {
		return nil, err
	}
	dir := filepath.Join(root, "codeagent-wrapper", "webui-v1")
	return publishBrowserLeaseAt(dir, port)
}

func publishBrowserLeaseAt(dir string, port int) (*browserLease, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		return nil, err
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return nil, err
	}
	id := hex.EncodeToString(nonce[:])
	lease := &browserLease{path: filepath.Join(dir, id+".json"), done: make(chan struct{}), exited: make(chan struct{})}
	write := func() error {
		data, err := json.Marshal(map[string]interface{}{"id": id, "url": fmt.Sprintf("http://127.0.0.1:%d/#ccg-task=%s", port, id), "updatedAt": time.Now().Unix()})
		if err != nil {
			return err
		}
		f, err := os.CreateTemp(dir, ".lease-")
		if err != nil {
			return err
		}
		defer os.Remove(f.Name())
		if _, err = f.Write(data); err != nil {
			f.Close()
			return err
		}
		if err = f.Close(); err != nil {
			return err
		}
		return os.Rename(f.Name(), lease.path)
	}
	if err := write(); err != nil {
		return nil, err
	}
	go func() {
		defer close(lease.exited)
		tick := time.NewTicker(2 * time.Second)
		defer tick.Stop()
		for {
			select {
			case <-lease.done:
				return
			case <-tick.C:
				if write() != nil {
					return
				}
			}
		}
	}()
	return lease, nil
}

func (l *browserLease) close() {
	if l == nil {
		return
	}
	l.once.Do(func() { close(l.done); <-l.exited; _ = os.Remove(l.path) })
}
