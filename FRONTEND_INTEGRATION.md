# Connecting the Lovable UI to the EmailOS AI backend

The Lovable frontend (`inbox-genius`) lives in its own private repo, so this file
delivers the code to paste in. It's **additive** — it doesn't change your design, and
it **falls back to your existing mock data** whenever the backend is unavailable.

## 1. Environment

Add to the frontend's `.env` (Vite):

```
VITE_API_BASE_URL=http://localhost:8000
```

Start the backend (`uvicorn app.main:app --reload`) before the UI, or leave it off —
the client will use mock data and the UI keeps working.

## 2. Drop in `src/lib/api.ts`

```ts
// src/lib/api.ts
// Typed client for the EmailOS AI backend, with graceful fallback to mock data.
import { toast } from "sonner";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

// ---- Types (mirror the backend Pydantic models) ----
export type Category =
  | "Need Reply" | "Important" | "FYI" | "Meeting" | "Payment"
  | "Product Access" | "Creator Partnership" | "Sales" | "Low Priority";
export type Priority = "High" | "Medium" | "Low";
export type DraftStatus =
  | "pending_review" | "approved" | "edited" | "ignored" | "regenerated";

export interface TriageResult {
  email_id: string;
  category: Category;
  priority: Priority;
  needs_reply: boolean;
  confidence: number;
  summary: string;
  why_it_matters: string;
  suggested_action: string;
  deadline_if_any?: string | null;
  risk_if_ignored: string;
}

export interface Evaluation {
  tone_score: number; clarity_score: number; completeness_score: number;
  context_score: number; risk_score: number; overall_score: number;
  feedback: string; should_rewrite: boolean;
}

export interface Critique {
  issues: string[]; missing_context: string[];
  tone_feedback: string; risk_feedback: string; rewrite_instructions: string;
}

export interface Draft {
  id: string;
  email_id: string;
  original_email_summary: string;
  subject_suggestion?: string | null;
  draft_body: string;
  tone: string;
  reasoning: string;
  status: DraftStatus;
  version: number;
  created_at: string;
  critique?: Critique | null;
  evaluation?: Evaluation | null;
  constraints_passed: boolean;
}

export interface FollowUp {
  email_id: string; sender_name: string; subject: string;
  reason: string; deadline_if_any?: string | null;
}

export interface DailyBrief {
  id: string;
  generated_at: string;
  top_priority: TriageResult[];
  need_reply: TriageResult[];
  drafts_ready: string[];
  follow_ups: FollowUp[];
  suggested_actions: string[];
  summary: string;
}

export interface AgentRun {
  id: string; started_at: string; completed_at?: string | null;
  emails_processed: number; drafts_created: number; errors: string[];
  total_cost_estimate: number; summary: string;
}

export interface TriageRunResponse {
  brief: DailyBrief;
  triage_results: TriageResult[];
  drafts: Draft[];
  follow_ups: FollowUp[];
  run: AgentRun;
}

export interface MemoryRule {
  id: string; situation: string; preference: string;
  example_good: string; example_bad: string; created_from: string; confidence: number;
}

// ---- Core fetch helper ----
export class BackendUnavailable extends Error {}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new BackendUnavailable(`Cannot reach backend at ${BASE}`);
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return (await res.json()) as T;
}

// Wrap a call so a missing backend transparently returns your mock fallback.
export async function withFallback<T>(
  call: () => Promise<T>,
  fallback: T,
  opts: { silent?: boolean } = {},
): Promise<T> {
  try {
    return await call();
  } catch (e) {
    if (e instanceof BackendUnavailable) {
      if (!opts.silent) toast.info("Backend offline — showing sample data");
      return fallback;
    }
    throw e;
  }
}

// ---- Endpoint functions ----
export const runTriage = () =>
  req<TriageRunResponse>("/api/agent/run-triage", { method: "POST" });

export const getBrief = () => req<DailyBrief>("/api/agent/brief");

export const getDrafts = () => req<Draft[]>("/api/drafts");
export const getDraft = (id: string) => req<Draft>(`/api/drafts/${id}`);

export const critiqueDraft = (id: string) =>
  req<Draft>(`/api/drafts/${id}/critique`, { method: "POST" });

export const regenerateDraft = (id: string, instruction?: string) =>
  req<Draft>(`/api/drafts/${id}/regenerate`, {
    method: "POST",
    body: JSON.stringify({ instruction: instruction ?? null }),
  });

export const approveDraft = (id: string) =>
  req<Draft>(`/api/drafts/${id}/approve`, { method: "POST" });

export const editDraft = (id: string, body: string, learn = false) =>
  req<{ draft: Draft; learn_result?: unknown }>(`/api/drafts/${id}/edit`, {
    method: "POST",
    body: JSON.stringify({ body, learn }),
  });

export const ignoreDraft = (id: string) =>
  req<Draft>(`/api/drafts/${id}/ignore`, { method: "POST" });

export const getMemoryRules = () => req<MemoryRule[]>("/api/memory/rules");
```

## 3. Wire the pages (minimal, additive)

Keep your existing components and `sonner` toasts. Only swap the data source.

- **Dashboard** — on mount, `getBrief()` for the summary cards; add a "Run triage"
  button that calls `runTriage()` then refreshes. Wrap reads in `withFallback(getBrief, mockBrief)`.
  ```ts
  const brief = await withFallback(getBrief, mockBrief);
  // top_priority / need_reply / drafts_ready / follow_ups / suggested_actions
  ```
- **Need Reply** — render `runTriage()`'s `triage_results.filter(t => t.needs_reply)`
  (or cache the last run). Each row: `category`, `priority`, `summary`, `suggested_action`.
- **Important** — `triage_results.filter(t => t.category === "Important" || t.priority === "High")`.
- **Drafts** — `getDrafts()`; show `draft_body`, `evaluation.overall_score`, and a
  `constraints_passed` badge. Wrap in `withFallback(getDrafts, mockDrafts)`.
- **DraftEditorModal** — buttons map directly:
  - Save → `editDraft(id, body, /* learn */ true)` then `toast.success("Draft saved")`
  - Regenerate → `regenerateDraft(id, instruction)` then refresh the modal
  - Approve → `approveDraft(id)` then `toast.success("Approved (not sent)")`
  - Ignore → `ignoreDraft(id)`
- **Follow-ups** — use `brief.follow_ups`.
- **Settings** — optional: show `GET /api/health` (mode: mock vs anthropic, model).

## 4. Loading & error states

Each call is a promise — wrap in your data layer (TanStack Query recommended):

```ts
const { data, isLoading, error } = useQuery({
  queryKey: ["drafts"],
  queryFn: () => withFallback(getDrafts, mockDrafts),
});
```

`withFallback` guarantees the UI still renders sample data when the backend is down, so
you never get a blank screen during development.

## 5. Note on hosting

When you deploy the backend, set `VITE_API_BASE_URL` to its public URL and make sure that
origin is covered by the backend's `ALLOWED_ORIGINS` (it already allows `*.lovable.app`).
