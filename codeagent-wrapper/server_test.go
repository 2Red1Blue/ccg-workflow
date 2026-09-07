package main

import (
	"os"
	"testing"
	"time"
)

func TestWebUIBrowserLaunchIsExplicitOptIn(t *testing.T) {
	key := "CODEAGENT_WEB_UI_AUTO_OPEN"
	previous, present := os.LookupEnv(key)
	t.Cleanup(func() {
		if present {
			_ = os.Setenv(key, previous)
		} else {
			_ = os.Unsetenv(key)
		}
	})

	for _, value := range []string{"", "1", "TRUE", "false"} {
		if err := os.Setenv(key, value); err != nil {
			t.Fatal(err)
		}
		if shouldAutoOpenWebUI() {
			t.Fatalf("value %q unexpectedly opens a browser", value)
		}
	}
	if err := os.Setenv(key, "true"); err != nil {
		t.Fatal(err)
	}
	if !shouldAutoOpenWebUI() {
		t.Fatal("true must opt in to browser launch")
	}
}

func TestWebServerOnlyLaunchesBrowserAfterExplicitOptIn(t *testing.T) {
	key := "CODEAGENT_WEB_UI_AUTO_OPEN"
	previous, present := os.LookupEnv(key)
	t.Cleanup(func() {
		if present {
			_ = os.Setenv(key, previous)
		} else {
			_ = os.Unsetenv(key)
		}
	})

	original := openBrowserFn
	t.Cleanup(func() { openBrowserFn = original })
	opened := make(chan string, 1)
	openBrowserFn = func(url string) { opened <- url }

	if err := os.Setenv(key, "false"); err != nil {
		t.Fatal(err)
	}
	server := NewWebServer("codex")
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	if err := server.Stop(); err != nil {
		t.Fatal(err)
	}
	select {
	case url := <-opened:
		t.Fatalf("default launch unexpectedly opened %s", url)
	case <-time.After(50 * time.Millisecond):
	}

	if err := os.Setenv(key, "true"); err != nil {
		t.Fatal(err)
	}
	server = NewWebServer("codex")
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	defer server.Stop()
	select {
	case <-opened:
	case <-time.After(time.Second):
		t.Fatal("explicit opt-in did not open the browser")
	}
}
