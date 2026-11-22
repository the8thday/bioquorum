import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MaterialHistory, type MaterialVersion } from "./Materials";

const first: MaterialVersion = {
  id:"v1", material_id:"m1", version:1, name:"samples.csv", kind:"vendor_delivery",
  status:"superseded", hash:"a".repeat(64), source:"Delivery 1", reason:"", withdrawal_reason:"",
  registered_at:"2026-11-20T10:00:00Z", registered_by:"user", file_id:"f1",
};
const second: MaterialVersion = {...first, id:"v2", version:2, file_id:"f2", hash:"b".repeat(64),
  source:"Delivery 2", reason:"Corrected two labels", status:"active"};

describe("Material history", () => {
  it("identifies both same-name versions, links to their own file and offers changes for the active version", () => {
    const html = renderToStaticMarkup(<MaterialHistory versions={[first, second]}
      base="/projects/p/api/programs/q/materials" editable onEdit={() => {}} />);
    expect(html).toContain("/projects/p/api/programs/q/materials/v1/file");
    expect(html).toContain("/projects/p/api/programs/q/materials/v2/file");
    for (const text of ["Delivery 1","Delivery 2","Corrected two labels",first.hash,second.hash]) expect(html).toContain(text);
    expect(html.match(/>Replace version</g)).toHaveLength(1);
    expect(html.match(/>Withdraw version</g)).toHaveLength(1);
  });
  it("retains a withdrawn version's reason and download, including on a closed program", () => {
    const html = renderToStaticMarkup(<MaterialHistory versions={[{...second,status:"withdrawn",withdrawal_reason:"Invalid batch"}]}
      base="/api/programs/q/materials" editable={false} onEdit={() => {}} />);
    expect(html).toContain("Invalid batch");
    expect(html).toContain("/api/programs/q/materials/v2/file");
    expect(html).not.toContain("Replace version");
    expect(html).not.toContain("Withdraw version");
  });
});
