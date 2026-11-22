import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Composer } from "./Composer";
import type { Snapshot } from "./types";

const baseRecord: Snapshot = {
  last_seq: 1,
  questions: [],
  turns: [],
  figures: [],
  answers: [],
  pending_plan: null,
  permissions: [],
  session_grants: [{ permission: "connector", target: "pubmed" }],
  activity: [],
  verification: [],
};

const models = [
  { spec: "openai:gpt-6.2-sol", allowed: true, reasoning: true },
  { spec: "deepseek:deepseek-chat", allowed: true, reasoning: false },
];

describe("Composer controls (ADR-051)", () => {
  it("shows reasoning effort picker for reasoning-capable models and hides it otherwise", () => {
    const withEffort = renderToStaticMarkup(
      <Composer
        draftPlan={async () => ({ steps: [], model: "" })}
        executing={false}
        connection="Connected"
        upload={null}
        openSettings={() => {}}
        attachments={[]}
        setAttachments={() => {}}
        refs={[]}
        setRefs={() => {}}
        worker="openai:gpt-6.2-sol"
        setWorker={() => {}}
        effort="high"
        setEffort={() => {}}
        defaultWorker="openai:gpt-6.2-sol"
        models={models}
        classification="public"
        record={baseRecord}
        example={null}
        preset={0}
        mutate={async () => {}}
        report={() => {}}
      />
    );
    expect(withEffort).toContain('aria-label="Reasoning effort"');
    expect(withEffort).toContain("Permissions");
    expect(withEffort).toContain("pill-count\">1<");

    const withoutEffort = renderToStaticMarkup(
      <Composer
        draftPlan={async () => ({ steps: [], model: "" })}
        executing={false}
        connection="Connected"
        upload={null}
        openSettings={() => {}}
        attachments={[]}
        setAttachments={() => {}}
        refs={[]}
        setRefs={() => {}}
        worker="deepseek:deepseek-chat"
        setWorker={() => {}}
        effort=""
        setEffort={() => {}}
        defaultWorker="openai:gpt-6.2-sol"
        models={models}
        classification="public"
        record={baseRecord}
        example={null}
        preset={0}
        mutate={async () => {}}
        report={() => {}}
      />
    );
    expect(withoutEffort).not.toContain('aria-label="Reasoning effort"');
  });

  it("renders three-way permission controls including session grant", () => {
    const html = renderToStaticMarkup(
      <Composer
        draftPlan={async () => ({ steps: [], model: "" })}
        executing={false}
        connection="Connected"
        upload={null}
        openSettings={() => {}}
        attachments={[]}
        setAttachments={() => {}}
        refs={[]}
        setRefs={() => {}}
        worker=""
        setWorker={() => {}}
        effort=""
        setEffort={() => {}}
        models={models}
        classification="public"
        record={{
          ...baseRecord,
          permissions: [
            { request_id: "r1", reason: "search_papers", permission: "connector", target: "pubmed" },
          ],
        }}
        example={null}
        preset={0}
        mutate={async () => {}}
        report={() => {}}
      />
    );
    expect(html).toContain("Allow for this session");
    expect(html).toContain("Allow once");
    expect(html).toContain("Deny");
  });
});
