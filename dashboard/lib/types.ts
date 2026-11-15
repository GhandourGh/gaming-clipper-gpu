export type JobSummary = {
  id: string; title: string; status: string; error?: string; created_at: string; style: string;
  clips_requested?: number; clip_count: number; ready_count: number; duration: number; cost_usd: number; thumbnail?: string;
};

export type Stage = {
  name: string; clip_idx: number; status: string; attempts: number; error?: string;
  result?: { seconds?: number }; started_at?: string; finished_at?: string;
};

export type Clip = {
  idx: number; status: string; review: string; review_note?: string;
  candidate: Record<string, unknown>; edl?: Record<string, unknown>; assets?: Record<string, unknown>;
  qc?: { passed?: boolean; checks?: Record<string, { ok: boolean; detail?: unknown }> };
  output_path?: string; file?: string; file_bytes?: number; thumbnail?: string; versions?: Array<Record<string, unknown>>;
};

export type JobDetail = {
  job: { id: string; title: string; status: string; error?: string; created_at: string; style: string; elapsed_s?: number; cost_usd: number; download_bytes?: number; source_available?: boolean;
    burned_captions?: { present: boolean; where?: string; coverage?: number; examples?: string[] } | null };
  clips: Clip[]; stages: Stage[];
  costs: Array<{ model: string; calls: number; tokens: number; usd: number }>;
  rejected: Array<{ idx: number; quality: number; why_engaging: string; title: string }>;
};
