import assert from "node:assert/strict";
import test from "node:test";

import { submitCorrections } from "./correctionsApi.js";

const payload = {
  sample_id: "sample",
  score_corrections: [
    {
      dimension_code: "A1-1",
      original_score: 2,
      corrected_score: 3,
    },
  ],
  feedback_corrections: [],
  evidence_additions: [],
};

test("submitCorrections falls back when a static server returns 501", async () => {
  const calls = [];
  const result = await submitCorrections(payload, {
    locationHref: "http://127.0.0.1:4174/frontend/index.html",
    fetchImpl: async (url, init) => {
      calls.push({ url: String(url), init });
      if (calls.length === 1) {
        return {
          ok: false,
          status: 501,
          json: async () => ({}),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, total_items: 1 }),
      };
    },
  });

  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, "http://127.0.0.1:4174/api/corrections");
  assert.equal(calls[1].url, "http://127.0.0.1:8000/api/corrections");
  assert.equal(calls[0].init.method, "POST");
  assert.equal(JSON.parse(calls[1].init.body).sample_id, "sample");
  assert.deepEqual(result, { ok: true, total_items: 1 });
});

test("submitCorrections uses same-origin endpoint when it succeeds", async () => {
  const calls = [];
  const result = await submitCorrections(payload, {
    locationHref: "http://127.0.0.1:8000/frontend/index.html",
    fetchImpl: async (url) => {
      calls.push(String(url));
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, total_items: 1 }),
      };
    },
  });

  assert.deepEqual(calls, ["http://127.0.0.1:8000/api/corrections"]);
  assert.deepEqual(result, { ok: true, total_items: 1 });
});
