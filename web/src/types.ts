export interface Project {
  first_pass?:boolean;
  project: string;
  classification: string;
  references?: boolean;
  plan_default?: boolean;
  onboarding?: {worker:"ready"|"restricted"|"unconfigured"; needs_local:boolean; disconnected:string[]};
  project_notes: boolean;
  worker_models: {spec: string; allowed: boolean}[];
  kernel_notice: string;
  sessions: {id: string; title: string}[];
  example: {question: string; steps: string[]} | null;
}

export interface StructurePick {type:"structure"; model:number; chain:string; label_chain:string; residue?:number; label_residue?:number; insertion?:string}
export interface TurnRef {kind: "answer" | "claim" | "figure" | "value" | "source" | "run" | "activity" | "artifact"; selection?:StructurePick|{type:"report";start:number;end:number}|{type:"genome"|"molecule";record:number}|{type:"figure";points:number[]}|{type:"table";row:number;column?:number}|{type:"sequence";record:number;start:number;end:number}; id: string; name?: string; excerpt?: string}

export type DraftReference = {item: TurnRef; label: string; note?: string};

export type EvidenceRef =
  | {type: "quote"; source: string; quote: string; call_id: string}
  | {type: "value"; name: string; run_id: string};

export interface Segment {text: string; raw?: string; problem?: string; ref?: EvidenceRef}
export interface Claim {
  needs_review?:boolean; replacement_claim_id?:string|null; replacement_session_id?:string|null;
  id: string;
  segments: Segment[];
  status: string;
  has_calculation: boolean;
  findings: {check: string; severity: string; evidence: string}[];
}
export interface CoverageLedger {
  topics: Record<string,string>;
  queries: {id:string; call_id:string; tool:string; time:string; scope:Record<string,unknown>;
    topic:string|null; count:number|null; status:string; reason:string}[];
  closed:boolean; gaps:string[]; decisive:string;
}
export interface Answer {
  first_pass?: boolean;
  discussion_items?: Record<string,string>;
  missing_sections?: string[];
  clarification?: {id:string;question:string;assumption:string}|null;
  coverage?: CoverageLedger|null;
  turn?:string|null;
  model?: string | null;
  id: string;
  seq: number;
  inherited: boolean;
  paragraphs: {unscoped_absence?:boolean; segments: Segment[]; unbound: string[]}[];
  claims: Claim[];
  review_completed?: boolean;
  review_limitations: string[];
}
export interface Plan {
  status: string; reason?: string; steps: string[]; done: number[];
  deviations: {change: string; reason: string}[];
}
export interface Turn {
  selections?:TurnRef[];
  question: string; plan?: Plan | null; status?: string; error?: string;
}
export interface Comparison {
  name: string; original: number | string | null; observed: number | string | null;
  unit?: string; observed_unit?: string; matched: boolean | null;
}
export type Verification =
  | {type: "rerun_finished"; result: {claim_id: string; outcome: string; status_eligible: boolean;
      runs: {reason?: string; values?: Comparison[]}[]; note?: string}}
  | {type: "replication_finished"; result: {claim_id: string; outcome: string;
      comparisons: Comparison[]; note?: string}};

export interface Snapshot {
  disconnected?: string[];
  last_seq: number;
  questions: {seq: number; text: string}[];
  turns: (Turn & {seq: number; id: string})[];
  products?: import("./Artifacts").Product[];
  figures: {id:string; seq: number; hash: string; run_id: string}[];
  answers: Answer[];
  pending_plan: string | null;
  verification: Verification[];
  permissions: {request_id: string; reason: string; permission: string; target: string}[];
  activity: (Activity & {id: string; time: string; seq: number})[];
}

export interface Activity {
  run_id?: string;
  type: string;
  tool?: string; replay_miss?: boolean; empty_result?: boolean; is_error?: boolean; error?: string;
  role?: string; model?: string; input_tokens?: number; output_tokens?: number;
  cost_known?: boolean; cost_usd?: number; granted?: boolean; permission?: string;
  step_index?: number; outcome?: string; question?: string; reason?: string;
  limitations?: string[]; finding_count?: number; where?: string;
}
