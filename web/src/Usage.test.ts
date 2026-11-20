import { expect, it } from "vitest";
import { costLabel } from "./Usage";

it("distinguishes measured zero from unknown and partially known prices", () => {
  const row = {calls: 2, input_tokens: 3, output_tokens: 4, cache_read_tokens: 0,
    cache_write_tokens: 0, latency_ms: 5, known_usd: 0, unknown_prices: 0, zero_prices: 2};
  expect(costLabel(row)).toBe("$0.000000");
  expect(costLabel({...row, unknown_prices: 2})).toBe("Price unavailable");
  expect(costLabel({...row, known_usd: .1, unknown_prices: 1})).toBe("$0.100000 + 1 unpriced");
});
