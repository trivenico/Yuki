package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/trivenico/Yuki/go/worker-health/internal/workerhealth"
)

func main() {
	baseURL := os.Getenv("YUKI_API_BASE_URL")
	token := os.Getenv("YUKI_ADMIN_TOKEN")
	interval := 15 * time.Second
	if raw := os.Getenv("YUKI_WORKER_HEALTH_INTERVAL"); raw != "" {
		seconds, err := strconv.Atoi(raw)
		if err != nil || seconds < 5 || seconds > 300 {
			fmt.Fprintln(os.Stderr, "YUKI_WORKER_HEALTH_INTERVAL must be 5..300 seconds")
			os.Exit(2)
		}
		interval = time.Duration(seconds) * time.Second
	}
	client, err := workerhealth.New(baseURL, token, 5*time.Second)
	if err != nil {
		fmt.Fprintln(os.Stderr, "worker health configuration invalid:", err)
		os.Exit(2)
	}
	defer client.Close()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		if err := report(ctx, client); err != nil {
			fmt.Fprintln(os.Stderr, "worker health check failed:", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func report(ctx context.Context, client *workerhealth.Client) error {
	snapshot, err := client.Fetch(ctx)
	if err != nil {
		return err
	}
	active, stale := 0, 0
	for _, worker := range snapshot.Workers {
		switch worker.Status {
		case "active":
			active++
		case "stale":
			stale++
		}
	}
	event := struct {
		Time    string `json:"time"`
		Workers int    `json:"workers"`
		Active  int    `json:"active"`
		Stale   int    `json:"stale"`
	}{time.Now().UTC().Format(time.RFC3339), len(snapshot.Workers), active, stale}
	encoder := json.NewEncoder(os.Stdout)
	if err := encoder.Encode(event); err != nil {
		return errors.New("could not write health event")
	}
	if stale > 0 {
		return fmt.Errorf("%d stale worker sessions reported", stale)
	}
	return nil
}
