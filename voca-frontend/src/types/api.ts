/** API types aligned with VOCA backend. */

export type SessionStatus =
  | "created"
  | "provider_lookup"
  | "dialing"
  | "negotiating"
  | "ranking"
  | "confirmed"
  | "failed"
  | "cancelled";

export interface SessionIntent {
  service_type: string;
  target_date: string | null;
  target_time: string | null;
  urgency: string | null;
  location_query: string | null;
  timezone: string | null;
}

export interface ProviderLocation {
  lat: number;
  lng: number;
}

export interface Provider {
  id: string;
  name: string;
  phone: string;
  rating: number;
  address: string;
  available_slots: Array<{ date: string; time: string; duration_min: number; doctor?: string }>;
  distance_km?: number;
  travel_time_min?: number;
  type?: string;
  location?: ProviderLocation;
}

export interface SquadPlan {
  session_id: string | null;
  intent: SessionIntent;
  providers: Provider[];
}

export interface Session {
  id: string;
  user_id: string;
  status: SessionStatus;
  service_type: string;
  query_text: string;
  created_at: string | null;
  updated_at: string | null;
  confirmed_call_task_id: string | null;
}

export interface CallTask {
  id: string;
  session_id: string;
  provider_id: string | null;
  provider_name: string | null;
  provider_phone: string | null;
  status: string;
  score: number | null;
  offered_date: string | null;
  offered_time: string | null;
  offered_duration_min: number | null;
  offered_doctor: string | null;
  hold_keys: string[];
  started_at: string | null;
  ended_at: string | null;
  updated_at: string | null;
  /** Optional for Best Matches UI — mock or from Places API */
  photo_url?: string | null;
  address?: string | null;
  lat?: number | null;
  lng?: number | null;
}

export interface StreamEvent {
  session_id: string;
  session_status: SessionStatus;
  updated_at: string | null;
  call_tasks: CallTask[];
  error?: string;
}

export interface SessionResults {
  session_id: string;
  offers: CallTask[];
}

export interface ConfirmResponse {
  status: "confirmed";
  call_task_id: string;
  calendar_synced: boolean;
  message: string;
}

export interface CancelResponse {
  status: "cancelled";
  message: string;
}

export interface Appointment {
  id: string;
  session_id: string;
  call_task_id: string;
  user_id: string;
  provider_id: string;
  provider_name: string;
  provider_phone: string;
  appointment_date: string;
  appointment_time: string;
  duration_min: number;
  doctor_name: string | null;
  calendar_synced: boolean;
  status: string;
  created_at: string | null;
  /** Optional for "View in Google" — built client-side if backend doesn't provide */
  calendar_link?: string | null;
}

export interface AppointmentsResponse {
  appointments: Appointment[];
}

export interface ApiErrorBody {
  detail: string | { msg?: string }[];
}
