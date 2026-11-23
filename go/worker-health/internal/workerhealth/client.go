package workerhealth

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	maxResponseBytes = 1 << 20
	maxTokenBytes    = 256
)

var ErrInvalidConfiguration = errors.New("invalid worker health client configuration")

type Worker struct {
	WorkerID   string         `json:"worker_id"`
	Status     string         `json:"status"`
	StartedAt  string         `json:"started_at"`
	LastSeenAt string         `json:"last_seen_at"`
	Metadata   map[string]any `json:"metadata"`
}

type Snapshot struct {
	Workers []Worker `json:"workers"`
}

type Client struct {
	endpoint string
	token    string
	http     *http.Client
}

func New(baseURL, token string, timeout time.Duration) (*Client, error) {
	if len(token) == 0 || len(token) > maxTokenBytes || strings.ContainsAny(token, " \r\n\t") {
		return nil, fmt.Errorf("%w: bearer token must be bounded and contain no whitespace", ErrInvalidConfiguration)
	}
	if timeout <= 0 || timeout > 30*time.Second {
		return nil, fmt.Errorf("%w: timeout must be in (0, 30s]", ErrInvalidConfiguration)
	}
	parsed, err := url.Parse(strings.TrimSpace(baseURL))
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return nil, fmt.Errorf("%w: base URL is invalid", ErrInvalidConfiguration)
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	if parsed.Scheme != "https" && !(parsed.Scheme == "http" && isLoopbackHost(parsed.Hostname())) {
		return nil, fmt.Errorf("%w: HTTPS is required except for loopback development", ErrInvalidConfiguration)
	}
	if parsed.Path == "" {
		parsed.Path = ""
	}
	parsed.Path += "/v1/admin/workers"
	transport := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: (&net.Dialer{
			Timeout: 5 * time.Second,
			KeepAlive: 30 * time.Second,
		}).DialContext,
		TLSHandshakeTimeout: 5 * time.Second,
		ResponseHeaderTimeout: timeout,
		MaxIdleConns: 10,
		IdleConnTimeout: 30 * time.Second,
	}
	client := &http.Client{
		Timeout: timeout,
		Transport: transport,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return &Client{endpoint: parsed.String(), token: token, http: client}, nil
}

func isLoopbackHost(host string) bool {
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback() || strings.EqualFold(host, "localhost")
}

func (c *Client) Close() {
	if c != nil && c.http != nil {
		if transport, ok := c.http.Transport.(*http.Transport); ok {
			transport.CloseIdleConnections()
		}
	}
}

func (c *Client) Fetch(ctx context.Context) (Snapshot, error) {
	var snapshot Snapshot
	if c == nil || c.http == nil {
		return snapshot, errors.New("worker health client is not initialized")
	}
	if ctx == nil {
		return snapshot, errors.New("worker health request context is required")
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.endpoint, nil)
	if err != nil {
		return snapshot, fmt.Errorf("create worker health request: %w", err)
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Accept", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return snapshot, fmt.Errorf("worker health request failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4096))
		return snapshot, fmt.Errorf("worker health endpoint returned HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxResponseBytes+1))
	if err != nil {
		return snapshot, fmt.Errorf("read worker health response: %w", err)
	}
	if len(body) > maxResponseBytes {
		return snapshot, errors.New("worker health response exceeds 1 MiB limit")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&snapshot); err != nil {
		return snapshot, fmt.Errorf("decode worker health response: %w", err)
	}
	if decoder.Decode(new(any)) != io.EOF {
		return snapshot, errors.New("worker health response contains trailing JSON")
	}
	for _, worker := range snapshot.Workers {
		if worker.WorkerID == "" || len(worker.WorkerID) > 200 {
			return Snapshot{}, errors.New("worker health response contains an invalid worker ID")
		}
		switch worker.Status {
		case "active", "stopping", "stopped", "stale":
		default:
			return Snapshot{}, errors.New("worker health response contains an unknown worker status")
		}
	}
	return snapshot, nil
}
