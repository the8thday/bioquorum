import { expect, it } from "vitest";
import { viewerFor } from "./registry";

it("keeps unsupported formats downloadable and loads supported viewers on demand", async () => {
  expect(viewerFor(null)).toBeUndefined();
  expect(viewerFor("unknown")).toBeUndefined();
  expect((await viewerFor("table")!.load()).default).toBeTypeOf("function");
});
