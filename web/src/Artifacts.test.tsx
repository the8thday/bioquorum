import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Artifacts, type Product } from "./Artifacts";
import { ResultViewer } from "./ResultViewer";

describe("Artifacts panel", () => {
  it("shows the generating turn number when present and omits it for uploads outside a turn", () => {
    const rows: Product[] = [
      { id: "f1", name: "counts.csv", kind: "table", version: 1, status: "registered", source: "user_upload", turn: null, turn_number: null, seq: 1, preview: "table" },
      { id: "fig1", name: "Figure r2", kind: "figure", version: 1, status: "recorded", turn: "t2", turn_number: 2, seq: 2, preview: "figure" },
    ];
    const html = renderToStaticMarkup(<Artifacts base="/api/sessions/s1" rows={rows} attach={() => {}} open={() => {}} />);
    expect(html).toContain("figure · v1 · recorded · Turn 2");
    expect(html).toContain("table · v1 · registered</p>");
  });

  it("shows the generating turn number in the ResultViewer header", () => {
    const item: Product = {
      id: "fig1", name: "Figure r2", kind: "figure", version: 1, status: "recorded",
      turn: "t2", turn_number: 2, seq: 2, preview: "figure",
    };
    const html = renderToStaticMarkup(
      <ResultViewer items={[item]} active={item} base="/api/sessions/s1" products={[item]}
        select={() => {}} inspector={() => {}} close={() => {}} open={() => {}} evidence={() => {}} />
    );
    expect(html).toContain("figure · v1 · recorded · Turn 2");
  });
});
