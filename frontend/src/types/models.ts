/** 与后端 schema 对应的领域类型。 */

export type Role = 'admin' | 'analyst' | 'client';

export interface UserInfo {
  id: number;
  email: string;
  display_name: string;
  role: Role;
  role_label: string;
  industry?: string | null;
  permissions?: string[];
}

export type SiteStatus = 'active' | 'degraded' | 'paused';
export type RunStatus = 'running' | 'success' | 'partial' | 'failed';
export type NoticeStatus = 'new' | 'tracking' | 'reviewing' | 'archived' | 'superseded' | 'withdrawn';
export type FieldKey = 'project_name' | 'budget_amount' | 'deadline' | 'opening_place';
export type FieldStatus = 'extracted' | 'verified' | 'corrected' | 'rejected';
export type RiskLevel = 'must_act' | 'watch' | 'skip' | 'unknown';
export type DeadlineKind = 'enroll' | 'bid_open' | 'qa';
export type TemplateStatus = 'draft' | 'active' | 'archived';
export type QualLevel = 'none' | 'grade_2' | 'grade_1' | 'grade_special';
export type VersionStatus = 'active' | 'superseded' | 'withdrawn';
export type ProjectStatus = 'active' | 'withdrawn';
export type ProjectSourceStatus = 'active' | 'superseded' | 'withdrawn';
export type DecisionStatus = 'auto' | 'manual' | 'conflict' | 'pending';
export type DecisionBasis =
  | 'single_source'
  | 'highest_confidence'
  | 'latest_published'
  | 'manual_confirm'
  | 'manual_override'
  | 'none';

export interface Site {
  id: number;
  name: string;
  domain: string;
  region: string;
  cadence_minutes: number;
  qps_limit: number;
  status: SiteStatus;
  priority: number;
  note?: string | null;
  notice_count: number;
  field_count: number;
  low_confidence_count: number;
  active_template_count: number;
}

export interface FieldRule {
  label?: string;
  anchors?: string[];
  patterns?: string[];
}

export interface Template {
  id: number;
  site_id: number;
  site_name: string;
  site_domain: string;
  name: string;
  version: number;
  status: TemplateStatus;
  field_rules: Record<string, FieldRule>;
  coverage_score: number;
  change_note?: string | null;
  created_by: string;
  created_at: string;
  published_at?: string | null;
}

export interface TemplateDiff {
  field_key: FieldKey;
  label: string;
  anchors_added: string[];
  anchors_removed: string[];
  pattern_changed: boolean;
  left_anchor_count: number;
  right_anchor_count: number;
  changed: boolean;
}

export interface TemplateCompare {
  left: Template;
  right: Template;
  coverage_delta: number;
  changed_field_count: number;
  diffs: TemplateDiff[];
}

export interface CrawlRun {
  id: number;
  status: RunStatus;
  triggered_by: string;
  site_count: number;
  fetched_count: number;
  inserted_count: number;
  duplicate_count: number;
  error_count: number;
  field_count: number;
  low_confidence_count: number;
  merged_count?: number;
  conflict_count?: number;
  withdrawn_count?: number;
  corrected_count?: number;
  crawl_ordinal?: number;
  phase?: string | null;
  duration_ms: number;
  note?: string | null;
  started_at: string;
  finished_at?: string | null;
}

export interface CrawlResult {
  run: CrawlRun;
  extraction: {
    field_count: number;
    created: number;
    updated: number;
    preserved_manual: number;
    low_confidence_count: number;
    notice_count: number;
  };
  merge?: {
    projects_created: number;
    sources_created: number;
    sources_updated: number;
    decisions_auto: number;
    decisions_conflict: number;
    decisions_pending: number;
    manual_preserved: number;
    withdrawn: number;
    corrected: number;
  };
  risk: {
    as_of: string;
    total: number;
    active_total?: number;
    inactive_total?: number;
    counts: Record<RiskLevel, number>;
  };
}

export interface FieldItem {
  id: number;
  notice_id: number;
  field_key: FieldKey;
  label: string;
  value_text?: string | null;
  raw_text?: string | null;
  unit?: string | null;
  amount_yuan?: number | null;
  amount_wan?: number | null;
  event_at?: string | null;
  precision: string;
  is_estimated: boolean;
  anchor_score: number;
  format_score: number;
  clarity_score: number;
  confidence: number;
  low_confidence: boolean;
  reason: string;
  status: FieldStatus;
  reviewer?: string | null;
  reviewed_at?: string | null;
  extracted_at: string;
  notice?: {
    id: number;
    external_id: string;
    title: string;
    region: string;
    category: string;
    industry: string;
    site_id: number;
    status: NoticeStatus;
    published_at: string;
  } | null;
}

export interface RiskInfo {
  risk_level: RiskLevel;
  risk_score: number | null;
  deadline_pressure: number | null;
  work_hours_remaining: number | null;
  event_at: string | null;
}

export interface NoticeAggregate {
  field_count: number;
  low_confidence_count: number;
  min_confidence: number;
  max_confidence: number;
  reviewed_count: number;
  budget_text?: string | null;
  budget_wan?: number | null;
  budget_display: string;
  deadline_text?: string | null;
  deadline_precision: string;
  deadline_is_estimated: boolean;
  deadline_missing: boolean;
  risk: RiskInfo | null;
}

export interface NoticeProjectContext {
  project_id: number;
  project_status: ProjectStatus;
  source_count: number;
  conflict_count: number;
  pending_field_count: number;
}

export interface NoticeVersion {
  version_no: number;
  status: VersionStatus;
  content_hash: string;
  change_note?: string | null;
  byte_size?: number;
  seen_at?: string | null;
  withdrawn_at?: string | null;
}

export interface NoticeItem {
  id: number;
  external_id: string;
  title: string;
  url: string;
  publisher: string;
  site_id: number;
  site_name: string;
  region: string;
  category: string;
  industry: string;
  qualification: QualLevel;
  status: NoticeStatus;
  body_kind: string;
  published_at: string;
  byte_size: number;
  crawl_run_id?: number | null;
  current_version_no?: number;
  withdrawn_at?: string | null;
  project?: NoticeProjectContext | null;
  aggregate: NoticeAggregate;
}

export interface BodyLine {
  text: string;
  fields: FieldKey[];
}

export interface DeadlineItem {
  id: number;
  kind: DeadlineKind;
  event_at?: string | null;
  precision: string;
  is_active?: boolean;
  inactive_reason?: string | null;
  risk_level: RiskLevel;
  risk_score?: number | null;
  deadline_pressure?: number | null;
  qualify_gap?: number | null;
  work_hours_remaining?: number | null;
  note?: string | null;
  computed_at: string;
}

export interface NoticeDetail extends NoticeItem {
  fields: FieldItem[];
  deadlines: DeadlineItem[];
  versions: NoticeVersion[];
  body_lines: BodyLine[];
  site: { id: number | null; name: string; domain: string; region: string };
  low_confidence_count: number;
}

export interface DecisionCandidate {
  notice_id: number;
  site_id: number;
  site_name: string;
  external_id: string;
  value_text?: string | null;
  amount_yuan?: number | null;
  event_at?: string | null;
  unit?: string | null;
  precision: string;
  is_estimated: boolean;
  confidence: number;
  field_status: FieldStatus;
  published_at: string;
  notice_status: NoticeStatus;
}

export interface ProjectDecision {
  id: number;
  project_id: number;
  field_key: FieldKey;
  label: string;
  status: DecisionStatus;
  status_label: string;
  basis: DecisionBasis;
  basis_label: string;
  value_text?: string | null;
  unit?: string | null;
  amount_yuan?: number | null;
  amount_wan?: number | null;
  event_at?: string | null;
  precision: string;
  is_estimated: boolean;
  confidence: number;
  source_notice_id?: number | null;
  source_site_id?: number | null;
  reason: string;
  reviewer?: string | null;
  reviewed_at?: string | null;
  candidates: DecisionCandidate[];
  conflict: boolean;
  pending: boolean;
}

export interface ProjectSourceVersion {
  version_no: number;
  status: VersionStatus;
  content_hash: string;
  change_note?: string | null;
  seen_at?: string | null;
  withdrawn_at?: string | null;
}

export interface ProjectSourceItem {
  id: number;
  site_id: number;
  site_name: string;
  site_domain: string;
  region: string;
  notice_id: number;
  external_id: string;
  status: ProjectSourceStatus;
  current_version_no: number;
  content_hash: string;
  first_seen_at?: string | null;
  last_seen_at?: string | null;
  withdrawn_at?: string | null;
  notice_status: NoticeStatus;
  notice_title: string;
  url: string;
  versions: ProjectSourceVersion[];
}

export interface ProjectDeadline {
  id: number;
  kind: DeadlineKind;
  event_at?: string | null;
  risk_level: RiskLevel;
  risk_score?: number | null;
  is_active: boolean;
  inactive_reason?: string | null;
  note?: string | null;
}

export interface ProjectSummary {
  id: number;
  merge_key: string;
  display_name: string;
  publisher: string;
  region: string;
  category: string;
  industry: string;
  qualification: QualLevel;
  status: ProjectStatus;
  status_label: string;
  primary_notice_id?: number | null;
  active_source_count: number;
  conflict_count: number;
  pending_field_count: number;
  withdrawn_at?: string | null;
  updated_at?: string | null;
  last_seen_at?: string | null;
  risk_level?: RiskLevel | null;
  risk_score?: number | null;
  deadline_event_active: boolean;
  decision_states: Record<string, DecisionStatus>;
}

export interface ProjectDetail extends ProjectSummary {
  sources: ProjectSourceItem[];
  decisions: ProjectDecision[];
  deadlines: ProjectDeadline[];
}

export interface ProjectList {
  items: ProjectSummary[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProjectStats {
  project_total: number;
  active_total: number;
  withdrawn_total: number;
  conflict_project_total: number;
  conflict_field_total: number;
  pending_field_total: number;
  manual_field_total: number;
  active_source_total: number;
}

export interface ProjectFilterOptions {
  regions: string[];
  categories: string[];
  statuses: ProjectStatus[];
  decision_status: Record<string, string>;
  basis: Record<string, string>;
}

export interface NoticeList {
  items: NoticeItem[];
  total: number;
  page: number;
  page_size: number;
  threshold: number;
}

export interface CalendarEvent {
  id: number;
  notice_id: number;
  project_id?: number | null;
  kind: DeadlineKind;
  event_at?: string | null;
  precision: string;
  is_active: boolean;
  inactive_reason?: string | null;
  work_hours_remaining?: number | null;
  deadline_pressure?: number | null;
  qualify_gap?: number | null;
  risk_score?: number | null;
  risk_level: RiskLevel;
  risk_label: string;
  note?: string | null;
  computed_at: string;
  notice: {
    id: number;
    external_id: string;
    title: string;
    region: string;
    category: string;
    industry: string;
    qualification: QualLevel;
    qualification_label: string;
    status: NoticeStatus;
    published_at: string;
  };
}

export interface CalendarPayload {
  items: CalendarEvent[];
  as_of: string;
  week_must_bid_count: number;
  formula: Record<string, string | number>;
}

export interface ReviewQueuePayload {
  threshold: number;
  pending_total: number;
  returned: number;
  reviewed_total: number;
  by_field: Record<string, number>;
  items: FieldItem[];
}

export interface GapCell {
  field_key: FieldKey;
  label: string;
  total: number;
  missing: number;
  low: number;
  avg_confidence: number;
  miss_rate: number;
}

export interface GapReport {
  threshold: number;
  sites: Array<{
    site_id: number;
    site_name: string;
    site_domain: string;
    region: string;
    cells: GapCell[];
  }>;
  overall: Array<{
    field_key: FieldKey;
    label: string;
    total: number;
    missing: number;
    low: number;
    miss_rate: number;
    avg_confidence: number;
  }>;
}

export interface Overview {
  notice_count: number;
  site_count: number;
  run_count: number;
  event_count: number;
  inactive_event_count?: number;
  field_total: number;
  field_low_confidence: number;
  review_pending: number;
  reviewed: number;
  missing_fields: number;
  avg_confidence: number;
  low_confidence_threshold: number;
  risk_counts: Record<RiskLevel, number>;
  budget_total_wan: number;
  latest_run: {
    id: number;
    status: RunStatus;
    fetched_count: number;
    inserted_count: number;
    duplicate_count: number;
    field_count: number;
    low_confidence_count: number;
    merged_count?: number;
    conflict_count?: number;
    withdrawn_count?: number;
    crawl_ordinal?: number;
    duration_ms: number;
    finished_at?: string | null;
  } | null;
}

export interface AuditLogItem {
  id: number;
  actor_email: string;
  actor_role: string;
  action: string;
  entity: string;
  entity_id?: string | null;
  detail: Record<string, unknown>;
  layer: string;
  created_at: string;
}
