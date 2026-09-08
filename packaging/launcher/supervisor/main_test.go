package main

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestManifestValidation(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "manifest.json")
	data := `{"schema_version":1,"app_id":"com.example.demo","display_name":"Demo","python":"3.12.10","entry_module":"demo.start","ready_path":"/health/ready","startup_timeout_seconds":180,"preferred_port":0,"data_directory_name":"Demo"}`
	if err := os.WriteFile(path, []byte(data), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := loadManifest(path); err != nil {
		t.Fatalf("valid manifest rejected: %v", err)
	}
}

func TestReadinessRejectsRemoteURL(t *testing.T) {
	if _, err := readinessURL("https://example.com:443", "/health/ready"); err == nil {
		t.Fatal("remote HTTPS URL accepted")
	}
	if got, err := readinessURL("http://127.0.0.1:8123", "/health/ready"); err != nil || got != "http://127.0.0.1:8123/health/ready" {
		t.Fatalf("loopback URL rejected: %q %v", got, err)
	}
}

func TestClassifyNetworkFailure(t *testing.T) {
	failure := classifyOutput(os.ErrDeadlineExceeded, "connection timed out")
	if failure.Code != "BOOT-NETWORK" || !failure.Retryable {
		t.Fatalf("unexpected classification: %#v", failure)
	}
}

func TestSanitizeRedactsMoreThanOneSecret(t *testing.T) {
	got := sanitize("token=first api_key=second password=third\nAuthorization: Bearer fourth")
	want := "token=[REDACTED] api_key=[REDACTED] password=[REDACTED]\nauthorization:[REDACTED]"
	if got != want {
		t.Fatalf("unexpected sanitized text: %q", got)
	}
}

func TestReadyMarkerRequiresExactFingerprint(t *testing.T) {
	path := filepath.Join(t.TempDir(), "READY")
	if err := os.WriteFile(path, []byte("expected\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if !readyMarkerValid(path, "expected") || readyMarkerValid(path, "different") {
		t.Fatal("READY marker did not require the exact fingerprint")
	}
}

func TestStatusRetryRequiresCapabilityToken(t *testing.T) {
	store := &statusStore{statusView: statusView{Stage: "Failed", Retryable: true}}
	retries := make(chan struct{}, 1)
	base, stop, err := serveStatus(store, retries)
	if err != nil {
		t.Fatal(err)
	}
	defer stop()
	response, err := http.Get(base + "api/status")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	var status struct {
		RetryToken string `json:"retry_token"`
	}
	if err := json.NewDecoder(response.Body).Decode(&status); err != nil || status.RetryToken == "" {
		t.Fatalf("status token missing: %q %v", status.RetryToken, err)
	}
	request, _ := http.NewRequest(http.MethodPost, base+"api/retry", nil)
	withoutToken, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, withoutToken.Body)
	withoutToken.Body.Close()
	if withoutToken.StatusCode != http.StatusForbidden {
		t.Fatalf("retry without token returned %d", withoutToken.StatusCode)
	}
	request, _ = http.NewRequest(http.MethodPost, base+"api/retry", nil)
	request.Header.Set("X-Goosnav-Retry", status.RetryToken)
	withToken, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	withToken.Body.Close()
	if withToken.StatusCode != http.StatusAccepted {
		t.Fatalf("retry with token returned %d", withToken.StatusCode)
	}
	select {
	case <-retries:
	default:
		t.Fatal("authorized retry was not delivered")
	}
}

func TestPruneKeepsCurrentAndPreviousReadyRuntime(t *testing.T) {
	root := t.TempDir()
	now := time.Now()
	for index, name := range []string{"current", "previous", "old"} {
		directory := filepath.Join(root, name)
		if err := os.Mkdir(directory, 0o700); err != nil {
			t.Fatal(err)
		}
		marker := filepath.Join(directory, "READY")
		if err := os.WriteFile(marker, []byte(name), 0o600); err != nil {
			t.Fatal(err)
		}
		stamp := now.Add(-time.Duration(index) * time.Hour)
		if err := os.Chtimes(marker, stamp, stamp); err != nil {
			t.Fatal(err)
		}
	}
	pruneRuntimes(root, "current")
	for _, name := range []string{"current", "previous"} {
		if _, err := os.Stat(filepath.Join(root, name)); err != nil {
			t.Fatalf("expected retained runtime %s: %v", name, err)
		}
	}
	if _, err := os.Stat(filepath.Join(root, "old")); !os.IsNotExist(err) {
		t.Fatalf("old runtime was not removed: %v", err)
	}
}

func TestFingerprintIgnoresSource(t *testing.T) {
	root := t.TempDir()
	for name, value := range map[string]string{"pyproject.toml": "[project]\nname='demo'\n", "uv.lock": "version = 1\n"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte(value), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	m := manifest{SchemaVersion: 1, Python: "3.12.10"}
	before, err := runtimeFingerprint(root, m, "linux-x64")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "source.py"), []byte("changed"), 0o600); err != nil {
		t.Fatal(err)
	}
	after, err := runtimeFingerprint(root, m, "linux-x64")
	if err != nil {
		t.Fatal(err)
	}
	if before != after {
		t.Fatal("source-only edit changed runtime fingerprint")
	}
}
