export type Scene = {
  falling_snow: string; intensity: string; visibility: string; snow_on_ground: string;
  large_snowdrifts: string; road_coverage: string; evidence: string; format_valid: boolean;
  model: string; prompt_version: string; snow_source?: 'scene' | 'snow' | null;
  details?: { blowing_snow: string; snowdrift_on_road: string; slush_on_road: string; road_obstruction: string;
    air_view: string; ground_view: string; road_view: string; roadside_view: string } | null;
};
export type Camera = {
  id: string; name: string; area: string; road: boolean; coordinates: [number, number] | null;
  district: string | null; section_name: string | null;
  analyzed_frame_id: string | null; analyzed_frame_at: string | null;
  analysis_stage: 'pending' | 'running' | 'done' | 'failed' | 'skipped' | null;
  location_note: string | null; status: string; last_frame: string | null; last_poll: string | null; source_at: string | null; frame_id: string | null; scene: Scene | null;
};
export type EventItem = {
  id: string; kind: string; name: string; area: string; severity: 'watch' | 'urgent'; reason: string;
  state: string; version: number; evidence_stale: boolean; opened_at: string; last_seen: string; ended_at: string | null;
  first_frame: string; latest_frame: string;
};
export type FeedbackVerdict = 'confirmed' | 'false_alarm' | 'already_working';
export type ObservedFrame = { id: string; observed_at: string };
export type SignalReview = {
  before: ObservedFrame | null;
  current: ObservedFrame | null;
  after: (ObservedFrame & { actor: string }) | null;
  candidate: ObservedFrame | null;
  feedback: { verdict: FeedbackVerdict; updated_at: string } | null;
};
export type Inspection = {
  event_id: string; name: string; area: string; severity: 'watch' | 'urgent'; last_seen: string; factors: string[];
};
export type DispatchLocation = { location_key: string; district: string; address: string };
export type RoadPoint = [number, number];
export type MapSection = { coordinates: RoadPoint[] };
export type DispatchRoad = MapSection & { id: string; name: string };
export type IssueKind = 'snowfall' | 'coverage' | 'drift' | 'other';
export type WorkKind = 'inspect' | 'clear';
export type ReportDraft = {
  id: string; location_key?: string; district?: string; address?: string; kind: IssueKind;
  description: string; channel: string; reference: string; severity: string;
  road_id?: string; endpoints?: [RoadPoint, RoadPoint];
  passage_obstructed?: boolean;
};
export type TaskDraft = { location_key: string; kind: IssueKind; work_kind?: WorkKind };
export type AssignmentDraft = { version: number; assignee: string; due_at: string; instructions: string };
export type TaskStage = 'draft' | 'sent' | 'accepted' | 'in_progress' | 'done' | 'returned' | 'closed';
export type TaskAction = 'accept' | 'start' | 'finish' | 'return' | 'close' | 'followup';
export type ProgressDraft = { version: number; action: TaskAction; note: string };
export type DispatchTask = {
  id: string; work_kind: WorkKind; created_at: string; assignee: string | null; due_at: string | null;
  assigned_at: string | null; accepted_at: string | null; response_seconds: number | null;
  overdue: boolean; assignment_version: number; claimed_by?: string | null; has_result_photo?: boolean; work_in_bot?: boolean;
  stage: TaskStage; instructions: string; result: string; return_note: string | null;
};
export type CrewTask = Pick<DispatchTask, 'id' | 'work_kind' | 'assignee' | 'due_at' | 'assignment_version' | 'stage' | 'instructions' | 'result' | 'return_note'> & {
  address: string; district: string; severity: 'urgent' | 'watch'; map_section: MapSection | null;
};
export type HistoryItem = {
  action: TaskAction | 'created' | 'assignment' | 'link_report'; actor: string; note: string; assignee: string | null;
  due_at: string | null; created_at: string; work_kind: WorkKind;
};
export type ArchivedTask = {
  id: string; address: string; district: string; result: string; work_kind: WorkKind; closed_at: string;
  claimed_by?: string | null; has_result_photo?: boolean;
  reports: { reference: string; description: string; resolution: string; closed_at: string | null }[];
};
type DispatchSource = { id: string; active: boolean; linked: boolean; last_seen: string };
export type DispatchGroup = DispatchLocation & {
  kind: IssueKind; severity: 'urgent' | 'watch'; last_seen: string;
  signals: (DispatchSource & { name: string; reason: string; road_priority_reason: string | null; frame_id: string | null })[];
  reports: (DispatchSource & { address: string; description: string; channel: string; reference: string; map_section: MapSection | null })[];
  task: DispatchTask | null;
};
export type BusObservation = {
  section_id: string; name: string; direction: 'forward' | 'reverse'; direction_label: string;
  coordinates?: RoadPoint[];
  status: 'check' | 'observed' | 'insufficient'; reason: 'buses' | 'camera' | 'report' | null;
  usual_seconds: number | null; observed_seconds: number | null; vehicle_count: number; last_seen: string | null;
};
export type BusObservations = {
  source: 'simulation';
  scenarios: { id: string; title: string; as_of: string; rows: BusObservation[] }[];
} | { source: 'rnis'; state: 'ready' | 'insufficient' | 'unavailable'; as_of: string | null; rows: BusObservation[] };
export type BusMap = {
  source: 'rnis' | 'simulation'; state: 'ready' | 'waiting' | 'insufficient' | 'unavailable';
  positions: { id: string; lon: number; lat: number; route: string | null; measured_at: string }[];
};
export type ActionItem = { request_id: string; actor: string; state: string; note: string; created_at: string };
export type Period = {
  at: string; horizon: 'daily' | 'hourly' | 'three_hourly'; probability: number | null; snow_mm: number | null; snow_cm?: number;
  snow: boolean; wind_mps: number | null; temperature_c: number | null;
};
export type Warning = { point: string; payload: Period & { start: string; end: string } };
export type Summary = {
  day: string; payload: {
    start: string; end: string; events: Pick<EventItem, 'id' | 'reason' | 'state' | 'name'>[];
    stale_cameras: string[]; warnings: Warning[];
    actions: (Pick<ActionItem, 'state' | 'note' | 'created_at'> & { name: string })[];
  };
};
export type Delivery = {
  id: number; kind: string; subject: string; body: string; stage: string; attempts: number;
  error: string | null; created_at: string;
};
export type SnowSegment = {
  id: string; name: string; osm_way: number | null; camera_id: string | null;
  calculation_source?: 'remote' | 'local';
  coordinates: [number, number][]; status: 'watch' | 'no_signal' | 'unavailable'; excess_cm: number | null;
};
export type SnowForecast = {
  status: 'ready' | 'partial' | 'unavailable' | 'pending' | 'running' | 'stale' | 'configuration_error';
  scope?: 'pilot' | 'city';
  remote_run_id?: string;
  counts?: Record<SnowSegment['status'], number>;
  calculated_at?: string; start?: string; end?: string; weather_sources?: string[];
  segments: SnowSegment[];
};
export type SnowRaster = { bbox_wgs84: [number, number, number, number]; png: string };
export type Dashboard = {
  mode: 'live' | 'preview' | 'demo' | 'replay'; run_id: string; role: 'viewer' | 'operator';
  cameras: Camera[]; events: EventItem[]; forecasts: { point: string; fetched_at: string; periods: Period[]; source?: string }[];
  warnings: Warning[]; summaries: Summary[]; deliveries: Delivery[];
  checks: { name: string; status: string; observed_at: string }[];
  inspection_queue: Inspection[];
  dispatch_queue: DispatchGroup[]; dispatch_locations: DispatchLocation[]; dispatch_assignees: string[];
  bus_observations?: BusObservations | null;
  bus_map?: BusMap | null;
  feedback_counts: Partial<Record<FeedbackVerdict, number>>;
  queue: { pending: number; oldest: string | null };
  snow_forecast?: SnowForecast | null;
  city_map_url?: string | null;
};
