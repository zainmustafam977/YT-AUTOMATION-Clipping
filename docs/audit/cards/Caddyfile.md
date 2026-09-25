# Assessment Cards: Caddyfile

---

## Unit 1: Reverse Proxy & Automated ACME TLS Gateway
| Field | Content |
|---|---|
| Location | `Caddyfile:1-20` |
| What it does | Configuration for Caddy 2 reverse proxy. Manages automated ACME TLS certificate issuance, proxies inbound HTTPS traffic to `dashboard:8000` with standard proxy forwarding headers, disables the local Caddy admin API to prevent runtime reconfiguration, and sets strict HTTP security headers (HSTS, nosniff, DENY, strict-origin). |
| Industrial-grade? | **A** - Hardened reverse proxy configuration with modern TLS security headers. |
| Checklist | **Error handling:** Pass (Caddy handles upstream dropouts). **Timeouts:** N/A (standard proxy defaults). **Retries:** N/A. **Idempotency:** Pass. **Input validation:** Pass. **Resource cleanup:** Pass. **Concurrency safety:** Pass (Caddy event-driven proxy). **Atomicity:** Pass. **Secret hygiene:** Pass (environment variable injection for domain and email). **Logging:** Standard. **Telemetry:** N/A. **Testability:** Pass. **Cost:** Free. **Modularity:** Pass. **Performance:** High. |
| Improvement proposals / Alternatives | Consider adding `Content-Security-Policy` and permissions policy headers for defense-in-depth against XSS. |
