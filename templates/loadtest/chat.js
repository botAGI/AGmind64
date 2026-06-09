// k6 chat load-test script (Phase 4.2) — STATIC + shippable.
//
// All knobs come from __ENV so the file never hard-codes an endpoint/model/load
// profile; `agmind loadtest chat` feeds them through `k6 run -e KEY=VALUE ...`.
// It hits an OpenAI-compatible /v1/chat/completions endpoint and emits the
// end-of-test summary as machine-parseable JSON via handleSummary(); the CLI
// reads that file back and renders p50/p95 latency, req/s, and error rate.
//
// Env knobs (with defaults matching the on-host llama-llm surface):
//   ENDPOINT  default http://127.0.0.1:8080/v1/chat/completions
//   MODEL     required (the served model id)
//   VUS       concurrent virtual users (default 5)
//   DURATION  constant-load duration, e.g. 30s / 2m (default 30s)
//   API_KEY   bearer token (default "dummy" — local llama needs none)
//   PROMPT    user message (default "ping")
//   SUMMARY   path the JSON summary is written to (default summary.json)

import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: Number(__ENV.VUS) || 5,
  duration: __ENV.DURATION || '30s',
};

export default function () {
  const url = __ENV.ENDPOINT || 'http://127.0.0.1:8080/v1/chat/completions';
  const payload = JSON.stringify({
    model: __ENV.MODEL,
    messages: [{ role: 'user', content: __ENV.PROMPT || 'ping' }],
    stream: false,
  });
  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${__ENV.API_KEY || 'dummy'}`,
    },
  };
  const res = http.post(url, payload, params);
  check(res, { 'status is 200': (r) => r.status === 200 });
}

// handleSummary returns a map of {path|'stdout': content}. Writing the parsed
// summary object as JSON to a file is format-stable across k6 versions (unlike
// the deprecated --summary-export flag), which is what the Python wrapper reads.
export function handleSummary(data) {
  const out = {};
  out[__ENV.SUMMARY || 'summary.json'] = JSON.stringify(data);
  return out;
}
