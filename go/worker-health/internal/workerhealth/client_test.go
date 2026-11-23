package workerhealth

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestNewRequiresTLSExceptLoopback(t *testing.T) {
	for _, tc := range []struct {
		name, base string
		wantErr bool
	}{
		{"https", "https://yuki.example", false},
		{"loopback", "http://127.0.0.1:8080", false},
		{"localhost", "http://localhost:8080", false},
		{"remote http", "http://yuki.example", true},
		{"userinfo", "https://user:pass@yuki.example", true},
		{"query", "https://yuki.example/?token=secret", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := New(tc.base, "test-token", time.Second)
			if (err != nil) != tc.wantErr {
				t.Fatalf("New() error = %v, wantErr %v", err, tc.wantErr)
			}
		})
	}
}

func TestFetchUsesBearerAndValidatesSnapshot(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/admin/workers" || r.Method != http.MethodGet {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Fatal("missing bearer authorization")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"workers":[{"worker_id":"worker-a","status":"active","started_at":"now","last_seen_at":"now","metadata":{"pool":"default"}}]}`))
	}))
	defer server.Close()
	client, err := New(server.URL, "test-token", time.Second)
	if err != nil { t.Fatal(err) }
	defer client.Close()
	got, err := client.Fetch(context.Background())
	if err != nil { t.Fatal(err) }
	if len(got.Workers) != 1 || got.Workers[0].WorkerID != "worker-a" {
		t.Fatalf("unexpected snapshot: %#v", got)
	}
}

func TestFetchRejectsOversizedAndUnknownStatus(t *testing.T) {
	for _, body := range []string{
		`{"workers":[{"worker_id":"a","status":"compromised"}]}`,
		`{"workers":[]}` + strings.Repeat(" ", maxResponseBytes),
	} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
			_, _ = w.Write([]byte(body))
		}))
		client, err := New(server.URL, "test-token", time.Second)
		if err != nil { t.Fatal(err) }
		_, err = client.Fetch(context.Background())
		client.Close()
		server.Close()
		if err == nil { t.Fatal("expected invalid response to be rejected") }
	}
}

func TestFetchDoesNotFollowRedirect(t *testing.T) {
	var targetCalled bool
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		targetCalled = true
	}))
	defer target.Close()
	source := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusFound)
	}))
	defer source.Close()
	client, err := New(source.URL, "test-token", time.Second)
	if err != nil { t.Fatal(err) }
	defer client.Close()
	if _, err := client.Fetch(context.Background()); err == nil {
		t.Fatal("expected redirect response to fail")
	}
	if targetCalled { t.Fatal("client followed redirect and risked forwarding credentials") }
}
