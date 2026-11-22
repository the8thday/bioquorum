import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BrandMark } from "./BrandMark";
import { AnswerCard, TurnCard } from "./Record";
import { Highlight } from "./Evidence";
import { activityLabel } from "./Activity";
import type { Answer } from "./types";

describe("record parity", () => {
  it("keeps an unmatched paragraph at assertion only and escapes its content", () => {
    const answer: Answer = {
      id: "a", seq: 1, inherited: false, review_limitations: ["Incomplete source"],
      paragraphs: [{ segments: [{ text: "<b>99</b>" }], unbound: ["99"] }], claims: [],
    };
    const html = renderToStaticMarkup(<AnswerCard answer={answer} example verification={[]}
      onEvidence={() => {}} onReview={() => {}} onVerify={() => {}} />);
    expect(html).toContain("Assertion only");
    expect(html).toContain("&lt;b&gt;99&lt;/b&gt;");
    expect(html).toContain("Unbound values or citations");
    expect(html).toContain("Incomplete source");
  });

  it("shows readable source links and groups evidence notes across paragraphs", () => {
    const url = "https://pubmed.ncbi.nlm.nih.gov/?term=IL11%20fibrosis";
    const answer: Answer = {
      id: "a", seq: 1, inherited: false, review_limitations: [], claims: [],
      paragraphs: [
        {segments: [{text: `见[原始研究](${url})。`}], unbound: ["11"]},
        {segments: [{text: "[unsafe](javascript:alert)"}], unbound: ["12"]},
      ],
    };
    const html = renderToStaticMarkup(<AnswerCard answer={answer} example verification={[]}
      onEvidence={() => {}} onReview={() => {}} onVerify={() => {}} />);
    expect(html).toContain(`href="${url}"`);
    expect(html).not.toContain('href="javascript:');
    expect(html.match(/Unbound values or citations/g)).toHaveLength(1);
    expect(html).toMatch(/<details[^>]*><summary>Unbound values or citations/);
  });

  it("keeps unfinished approved plan steps visible after completion", () => {
    const html = renderToStaticMarkup(<TurnCard turn={{question: "Compare counts", status: "completed",
      plan: {status: "approved", steps: ["Count"], done: [], deviations: []}}} />);
    expect(html).toContain("Some plan steps were not reported complete.");
  });

  it("highlights the recorded wording across whitespace and literal punctuation", () => {
    expect(renderToStaticMarkup(<Highlight source={'a (value)\nper cell'} quote="(value) per" />))
      .toContain("<mark>(value)\nper</mark>");
  });

  it("preserves unknown price and empty search results in activity", () => {
    expect(activityLabel({type: "model_call_finished", role: "worker", input_tokens: 2,
      output_tokens: 3, cost_known: false, cost_usd: 0})).toContain("5 tokens · Price unavailable");
    expect(activityLabel({type: "tool_call_finished", tool: "search", empty_result: true}))
      .toBe("search · No items returned");
  });
});

describe("claim verification controls", () => {
  const claim = {
    id: "c1", status: "reproduced", has_calculation: true,
    segments: [{text: "Bound measurement"}], findings: [],
  };
  const answer: Answer = {
    id: "a", seq: 4, inherited: false, review_limitations: [],
    paragraphs: [{segments: claim.segments, unbound: []}], claims: [claim],
  };
  function render(inherited: boolean, status: string) {
    return renderToStaticMarkup(<AnswerCard answer={{...answer, inherited, claims: [{...claim, status}]}}
      example={false} verification={[]} onEvidence={() => {}} onReview={() => {}} onVerify={() => {}}/>);
  }
  it("displays material review separately from the historical trust grade and links the corrected claim", () => {
    const row = {...claim, needs_review:true, replacement_claim_id:"new-claim", replacement_session_id:"new-session"};
    const html=renderToStaticMarkup(<AnswerCard answer={{...answer, claims:[row]}} example={false} verification={[]}
      onEvidence={()=>{}} onReview={()=>{}} onVerify={()=>{}}/>);
    expect(html).toContain("Reproduced");
    expect(html).toContain("Needs review");
    expect(html).toContain("?session=new-session#claim-new-claim");
  });
  it("offers replication only after reproduction and only on a local claim", () => {
    expect(render(false, "reviewed")).toContain("Rerun calculation");
    expect(render(false, "reviewed")).not.toContain("Independent replication");
    expect(render(false, "reproduced")).toContain("Independent replication");
    expect(render(true, "reproduced")).not.toContain("Rerun calculation");
    expect(render(true, "reproduced")).not.toContain("Run review");
  });
});

it("labels past tool requests as entries in the activity history", () => {
  expect(activityLabel({type:"tool_call_requested",tool:"export_research_bundle"})).toBe("Requested export_research_bundle");
});

it("renders emphasis and inline code while keeping model text escaped", () => {
  const answer: Answer = {id:"a",seq:1,inherited:false,review_limitations:[],claims:[],
    paragraphs:[{segments:[{text:"The **verified** column is `<count>`."}],unbound:[]}]};
  const html = renderToStaticMarkup(<AnswerCard answer={answer} example verification={[]}
    onEvidence={()=>{}} onReview={()=>{}} onVerify={()=>{}}/>);
  expect(html).toContain("<strong>verified</strong>");
  expect(html).toContain("<code>&lt;count&gt;</code>");
});

it("offers a side view for answer tables and keeps bound values actionable", () => {
  const answer: Answer = {id:"t",seq:1,inherited:false,claims:[],review_limitations:[],
    paragraphs:[[{text:"| Sample | Count |"}],[{text:"| --- | ---: |"}],
      [{text:"| A | "},{text:"7",ref:{type:"value" as const,run_id:"r",name:"count"}},{text:" |"}]]
      .map(segments=>({segments,unbound:[]}))};
  const html=renderToStaticMarkup(<AnswerCard answer={answer} example verification={[]} openTable={()=>{}}
    onEvidence={()=>{}} onReview={()=>{}} onVerify={()=>{}}/>);
  expect(html).toContain("Open answer table 1");
  expect(html).toContain('<button class="evidence-link">7</button>');
  expect(html).toContain("Original rows and evidence status");
});

describe("coverage in the research record", () => {
  it("shows observed empty results, open gaps and unscoped claims", () => {
    const answer: Answer = {id:"a", seq:1, inherited:false, review_limitations:[], claims:[],
      paragraphs:[{segments:[{text:"There is no evidence."}],unbound:[],unscoped_absence:true}],
      coverage:{topics:{trials:"Which trials?"},closed:false,gaps:["No outcome data"],decisive:"",
        queries:[{id:"q1",call_id:"real",scope:{query:"sample term"},topic:"trials",tool:"search",
          time:"2026-11-20",count:0,status:"empty",reason:""}]}};
    const html=renderToStaticMarkup(<AnswerCard answer={answer} example verification={[]}
      onEvidence={()=>{}} onReview={()=>{}} onVerify={()=>{}}/>);
    for (const text of ["sample term","Which trials?","No outcome data","No records in this search","matching scoped query"])
      expect(html).toContain(text);
  });
});

describe("brand mark", () => {
  it("renders the vector antibody-Q mark with unique gradient ids across instances", () => {
    const html = renderToStaticMarkup(<div><BrandMark className="brand-mark"/><BrandMark className="answer-mark"/></div>);
    const ids = [...html.matchAll(/linearGradient id="([^"]+)"/g)].map(m => m[1]);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
    expect(html).toContain('class="brand-mark"');
    expect(html).toContain('class="answer-mark"');
    expect(html).toContain('aria-hidden="true"');
  });

  it("uses the vector mark on answer headers", () => {
    const answer: Answer = {id:"a", seq:1, inherited:false, review_limitations:[], claims:[],
      paragraphs:[{segments:[{text:"Observation."}],unbound:[]}]};
    const html = renderToStaticMarkup(<AnswerCard answer={answer} example verification={[]}
      onEvidence={()=>{}} onReview={()=>{}} onVerify={()=>{}}/>);
    expect(html).toContain('class="answer-mark"');
    expect(html).toContain("<svg");
  });
});
