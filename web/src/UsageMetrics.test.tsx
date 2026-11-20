import { expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { UsageMetrics } from "./Usage";

it("renders intact values for narrow inspectors and keeps unpriced calls explicit", () => {
  const total = {calls: 5, input_tokens: 14472, output_tokens: 632, cache_read_tokens: 13789,
    cache_write_tokens: 323, latency_ms: 34970, known_usd: 0, unknown_prices: 5, zero_prices: 0};
  const html = renderToStaticMarkup(<UsageMetrics scope={{total, roles: {worker: total}}}/>);
  expect(html).toContain("14,472");
  expect(html).toContain("13,789");
  expect(html).toContain("34.97 s");
  expect(html).toContain("Price unavailable");
  expect(html).toContain("worker");
  expect(html).not.toContain("<table");
});

it("charts input and output without adding cached input twice", () => {
  const total = {calls:1,input_tokens:300,output_tokens:100,cache_read_tokens:200,cache_write_tokens:10,
    latency_ms:100,known_usd:0,unknown_prices:1,zero_prices:0};
  const html = renderToStaticMarkup(<UsageMetrics scope={{total,roles:{}}}/>);
  expect(html).toContain('aria-label="Input tokens, 75%. Output tokens, 25%."');
});
