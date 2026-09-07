package main

import (
	"encoding/json"
	"os"
	"testing"
)

func TestBrowserLeaseTracksOnlyItsTaskLifecycle(t *testing.T) {
	lease, err := publishBrowserLeaseAt(t.TempDir(), 12345)
	if err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(lease.path)
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]interface{}
	if err := json.Unmarshal(data, &record); err != nil {
		t.Fatal(err)
	}
	if record["url"] != "http://127.0.0.1:12345/#ccg-task="+record["id"].(string) {
		t.Fatal(record)
	}
	lease.close()
	lease.close()
	if _, err := os.Stat(lease.path); !os.IsNotExist(err) {
		t.Fatal("lease survived task completion")
	}
}
