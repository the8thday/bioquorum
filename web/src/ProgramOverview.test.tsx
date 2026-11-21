import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ComparisonTable } from "./MaterialImpact";
import { DecisionEntry, type Decision } from "./ProgramOverview";

const original:Decision = {
  id:"decision", revision:"record-id", text:"Repeat the control arm.", needs_review:true, history:[],
  claims:[{id:"claim", revision:"state", session_id:"s", source_event_id:"a", text:"Control mean was 3.",status:"grounded"}],
};

describe("Research line correction", () => {
  it("shows both recorded values, the new trust level and the historical trust level", () => {
    const html = renderToStaticMarkup(<ComparisonTable comparisons={[{
      old_claim_id:"old", new_claim_id:"new", old_text:"Mean was 3.", new_text:"Mean is 8.",
      old_status:"reviewed",new_status:"grounded",values:[{name:"mean",old_value:3,new_value:8,unit:"signals"}],
    }]}/>);
    for (const text of ["Mean was 3.", "Mean is 8.","reviewed","grounded","3 signals","8 signals"]) expect(html).toContain(text);
  });
  it("preserves the fixed basis for decisions and displays a separate review notice", () => {
    const html = renderToStaticMarkup(<DecisionEntry decision={original} showRecord={() => {}}/>);
    for (const text of ["Repeat the control arm.", "Control mean was 3.", "Needs review", "grounded"]) expect(html).toContain(text);
    expect(html).not.toContain("Revise decision");
  });
});
