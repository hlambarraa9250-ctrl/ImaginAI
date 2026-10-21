/**
 * imaginAI Client API Utilities
 */

export interface CaptionConfig {
  enabled?: boolean;
  color?: string;
  effect?: string;
  font?: string;
  size_percent?: number;
  position?: string;
}

export interface JobRequest {
  idea: string;
  duration: "5_min" | "8_min" | "10_min" | "15_min" | "short" | "medium";
  style?: "mystery" | "listicle" | "documentary" | "motivational";
  voice_provider?: "gemini" | "fish-audio" | "edge-tts" | "gtts";
  voice?: string;
  niche_id?: string;
  custom_niche?: string;
  custom_niche_description?: string;
  niche_sources?: string[];
  music_track?: string;
  input_mode?: "ai_generated" | "script_provided" | "idea" | "script";
  provided_script?: string;
  additional_context?: string;
  caption_config?: CaptionConfig;
  domain?: string;
  niche?: string;
  project_id?: string;
}

export interface SubNiche {
  id: string;
  title: string;
  desc: string;
  prompt_instructions?: string;
  visual_style?: string;
  music_style?: string;
}

export interface DomainCategory {
  id: string;
  title: string;
  icon: string;
  color: string;
  sub_niches: SubNiche[];
}

export async function getNiches(): Promise<{ domains: DomainCategory[] }> {
  const resp = await fetch(`${API_BASE}/api/niches`, { cache: "no-store" });
  if (!resp.ok) {
    throw new Error("Failed to fetch niches catalog");
  }
  return resp.json();
}

export interface VoiceItem {
  id: string;
  name: string;
  gender: "male" | "female" | "neutral";
  lang: string;
  desc: string;
}

export interface VoiceProviderCatalog {
  provider: "gemini" | "fish-audio" | "edge-tts" | "gtts";
  provider_name: string;
  desc: string;
  voices: VoiceItem[];
}


export interface JobStatusResponse {
  job_id: string;
  status: "pending" | "processing" | "pending_section_approval" | "pending_review" | "done" | "failed";
  current_stage?: string;
  section_proposal?: any;
  stages: {
    script: "pending" | "processing" | "done" | "failed";
    tts: "pending" | "processing" | "done" | "failed";
    footage: "pending" | "processing" | "done" | "failed";
    captions: "pending" | "processing" | "done" | "failed";
    render: "pending" | "processing" | "done" | "failed";
  };
  error?: string;
  output_url?: string;
}

// The public Next.js app proxies /api and /health to the internal FastAPI
// service. An override is retained for local split-server development only.
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export class ApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

function getOrCreateVisitorId(): string {
  if (typeof window === "undefined") return "server_ssr";
  let vid = localStorage.getItem("imaginai_visitor_id");
  if (!vid) {
    vid = "v_" + Math.random().toString(36).substring(2, 11) + Date.now().toString(36);
    localStorage.setItem("imaginai_visitor_id", vid);
  }
  return vid;
}

export async function createJob(data: JobRequest, idempotencyKey?: string): Promise<{ job_id: string }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/jobs`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
      ...(idempotencyKey ? { "x-idempotency-key": idempotencyKey } : {}),
    },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    let errDetail = "فشل إنشاء طلب الفيديو";
    try {
      const errJson = await resp.json();
      if (errJson.detail) errDetail = errJson.detail;
    } catch {
      errDetail = resp.statusText || errDetail;
    }
    throw new Error(errDetail);
  }
  return resp.json();
}

export async function sendFeedback(message: string, contact?: string): Promise<{ message: string }> {
  const resp = await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, contact }),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.detail || "فشل إرسال الملاحظات");
  }
  return data;
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/jobs/${jobId}/status`, {
    cache: "no-store",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => null);
    throw new ApiError(
      typeof body?.detail === "string" ? body.detail : `تعذر جلب حالة المهمة (${resp.status}).`,
      resp.status,
    );
  }
  return resp.json();
}

export function getDownloadUrl(jobId: string): string {
  return `${API_BASE}/api/jobs/${jobId}/download`;
}

export async function getVoicesCatalog(projectId?: string): Promise<{ providers: VoiceProviderCatalog[] }> {
  const visitorId = getOrCreateVisitorId();
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : "";
  const resp = await fetch(`${API_BASE}/api/voices${query}`, {
    cache: "no-store",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });
  if (!resp.ok) {
    throw new Error("Failed to fetch voices catalog");
  }
  return resp.json();
}

export interface Project {
  project_id: string;
  device_id: string;
  domain: string;
  niche: string;
  name: string;
  created_at: string;
  video_count?: number;
  last_video_at?: string;
}

export interface VideoItem {
  video_id: string;
  project_id: string;
  title: string;
  file_path?: string;
  duration: number;
  created_at: string;
  status: "processing" | "completed" | "failed" | "pending_review";
}

export async function getProjects(): Promise<{ projects: Project[] }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/projects`, {
    cache: "no-store",
    credentials: "include",
    headers: { 
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });
  if (!resp.ok) {
    throw new Error("Failed to fetch projects list");
  }
  return resp.json();
}

export async function createProject(data: { domain: string; niche: string; name: string }): Promise<Project> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/projects`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
    body: JSON.stringify(data),
  });
  if (!resp.ok) {
    throw new Error("Failed to create project");
  }
  return resp.json();
}

export async function renameProject(projectId: string, name: string): Promise<Project> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
    body: JSON.stringify({ name }),
  });
  if (!resp.ok) throw new Error("تعذر إعادة تسمية المشروع.");
  return resp.json();
}

export async function deleteProject(projectId: string): Promise<{ success: boolean; deleted_videos: number }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/projects/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || "تعذر حذف المشروع.");
  }
  return resp.json();
}

export async function renameProjectVoice(projectId: string, voiceId: string, name: string): Promise<void> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(projectId)}/voices/${encodeURIComponent(voiceId)}`,
    {
      method: "PATCH",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        "x-visitor-id": visitorId,
        "x-device-id": visitorId,
      },
      body: JSON.stringify({ name }),
    },
  );
  if (!resp.ok) throw new Error("تعذرت إعادة تسمية الصوت في هذا المشروع.");
}

export async function hideProjectVoice(projectId: string, voiceId: string): Promise<void> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(
    `${API_BASE}/api/projects/${encodeURIComponent(projectId)}/voices/${encodeURIComponent(voiceId)}`,
    {
      method: "DELETE",
      credentials: "include",
      headers: {
        "x-visitor-id": visitorId,
        "x-device-id": visitorId,
      },
    },
  );
  if (!resp.ok) throw new Error("تعذر إخفاء الصوت من هذا المشروع.");
}

export async function getProjectVideos(projectId: string): Promise<{ project: Project; videos: VideoItem[] }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/projects/${projectId}`, {
    cache: "no-store",
    credentials: "include",
    headers: { 
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });
  if (!resp.ok) {
    throw new Error("Failed to fetch project videos");
  }
  return resp.json();
}

export function getVideoStreamUrl(videoId: string): string {
  return `${API_BASE}/api/videos/${videoId}/stream`;
}

export function getVoicePreviewUrl(provider: string, voiceId: string): string {
  return `${API_BASE}/api/voices/preview?provider=${encodeURIComponent(provider)}&voice=${encodeURIComponent(voiceId)}`;
}

export async function cloneVoice(file: File, voiceName: string): Promise<{ success: boolean; voice: any }> {
  const visitorId = getOrCreateVisitorId();
  const formData = new FormData();
  formData.append("file", file);
  formData.append("voice_name", voiceName);

  const resp = await fetch(`${API_BASE}/api/voices/clone`, {
    method: "POST",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
    body: formData,
  });

  if (!resp.ok) {
    let errDetail = "فشل استنسخ الصوت";
    try {
      const errJson = await resp.json();
      if (errJson.detail) errDetail = errJson.detail;
    } catch {
      errDetail = resp.statusText || errDetail;
    }
    throw new Error(errDetail);
  }

  return resp.json();
}

export interface CustomFont {
  id: string;
  device_id: string;
  font_name: string;
  file_path: string;
  created_at: string;
}

export async function uploadCustomFont(file: File, fontName: string): Promise<{ success: boolean; font: CustomFont }> {
  const visitorId = getOrCreateVisitorId();
  const formData = new FormData();
  formData.append("file", file);
  formData.append("font_name", fontName);

  const resp = await fetch(`${API_BASE}/api/fonts/upload`, {
    method: "POST",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
    body: formData,
  });

  if (!resp.ok) {
    let errDetail = "فشل رفع الخط";
    try {
      const errJson = await resp.json();
      if (errJson.detail) errDetail = errJson.detail;
    } catch {
      errDetail = resp.statusText || errDetail;
    }
    throw new Error(errDetail);
  }

  return resp.json();
}

export async function getCustomFonts(): Promise<{ fonts: CustomFont[] }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/fonts`, {
    cache: "no-store",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });

  if (!resp.ok) {
    throw new Error("Failed to fetch custom fonts");
  }

  return resp.json();
}

export interface ReviewShot {
  shot_index: number;
  clip_path?: string;
  stream_url: string;
  metadata?: {
    provider?: string;
    providerAssetId?: string;
    title?: string;
    sourcePageUrl?: string;
    licenseName?: string;
    licenseUrl?: string;
    rightsStatus?: "valid" | "needs_review" | string;
    requiresAttribution?: boolean;
    attributionText?: string;
    isRealHistoricalFootage?: boolean;
    isIllustrativeFootage?: boolean;
  };
}

export interface ReviewScene {
  scene_index: number;
  narration: string;
  visual_keywords: string[];
  audio_path: string;
  audio_duration: number;
  shots: ReviewShot[];
}

export interface ReviewData {
  job_id: string;
  idea?: string;
  duration?: string;
  style?: string;
  voice?: string;
  voice_provider?: string;
  scenes: ReviewScene[];
}

export async function getJobReviewData(jobId: string): Promise<ReviewData> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/jobs/${jobId}/review`, {
    cache: "no-store",
    credentials: "include",
    headers: {
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
  });

  if (!resp.ok) {
    throw new Error("فشل جلب بيانات المراجعة");
  }

  return resp.json();
}

export async function resumeJob(
  jobId: string,
  editedScenes: { scene_index: number; narration?: string; shots?: string[] }[],
  licenseReviewConfirmed = false
): Promise<{ success: boolean; message: string }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/jobs/${jobId}/resume`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
    body: JSON.stringify({
      edited_scenes: editedScenes,
      license_review_confirmed: licenseReviewConfirmed,
    }),
  });

  if (!resp.ok) {
    let errMsg = "فشل استئناف عملية الرندر";
    try {
      const errJson = await resp.json();
      if (errJson.detail) errMsg = errJson.detail;
    } catch {
      errMsg = resp.statusText || errMsg;
    }
    throw new Error(errMsg);
  }

  return resp.json();
}

export async function approveJobSections(
  jobId: string,
  applySections: boolean
): Promise<{ success: boolean; message: string }> {
  const visitorId = getOrCreateVisitorId();
  const resp = await fetch(`${API_BASE}/api/jobs/${jobId}/approve-sections`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "x-visitor-id": visitorId,
      "x-device-id": visitorId,
    },
    body: JSON.stringify({ apply_sections: applySections }),
  });

  if (!resp.ok) {
    let errMsg = "فشل إرسال قرار الأقسام";
    try {
      const errJson = await resp.json();
      if (errJson.detail) errMsg = errJson.detail;
    } catch {
      errMsg = resp.statusText || errMsg;
    }
    throw new Error(errMsg);
  }

  return resp.json();
}

export type ProviderKeyStatus = "untested" | "valid" | "invalid" | "rate_limited" | "unauthorized" | "unavailable";
export interface ProviderKeyMetadata {
  key_id: string;
  status: ProviderKeyStatus;
  last_tested_at?: string | null;
  created_at: string;
}
export interface ApiKeySettings {
  providers: Record<string, { configured: boolean; keys: ProviderKeyMetadata[] }>;
}

function deviceHeaders(): HeadersInit {
  const visitorId = getOrCreateVisitorId();
  return { "x-visitor-id": visitorId, "x-device-id": visitorId };
}

export async function getApiKeySettings(): Promise<ApiKeySettings> {
  const resp = await fetch(`${API_BASE}/api/settings/api-keys`, {
    cache: "no-store",
    credentials: "include",
    headers: deviceHeaders(),
  });
  if (!resp.ok) throw new Error("فشل تحميل إعدادات مفاتيح الخدمات");
  return resp.json();
}

export async function saveApiKey(provider: string, apiKey: string): Promise<{ key_id: string; status: string }> {
  const resp = await fetch(`${API_BASE}/api/settings/api-keys`, {
    method: "POST",
    credentials: "include",
    headers: { ...deviceHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "فشل حفظ المفتاح");
  return data;
}

export async function deleteApiKey(provider: string, keyId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/settings/api-keys/${encodeURIComponent(provider)}/${encodeURIComponent(keyId)}`, {
    method: "DELETE",
    credentials: "include",
    headers: deviceHeaders(),
  });
  if (!resp.ok) throw new Error("فشل حذف المفتاح");
}

export async function testApiKey(provider: string, keyId: string): Promise<{ status: ProviderKeyStatus; message: string }> {
  const resp = await fetch(`${API_BASE}/api/settings/api-keys/test`, {
    method: "POST",
    credentials: "include",
    headers: { ...deviceHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ provider, key_id: keyId }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "فشل اختبار المفتاح");
  return data;
}

export interface VideoSourceInfo {
  id: string;
  label: string;
  requires_key: boolean;
  enabled: boolean;
  connectionStatus: "disabled" | "missing_key" | "configured_not_tested" | "not_tested";
}

export async function getVideoSources(): Promise<{ sources: VideoSourceInfo[] }> {
  const resp = await fetch(`${API_BASE}/api/video-sources`, {
    cache: "no-store",
    credentials: "include",
    headers: deviceHeaders(),
  });
  if (!resp.ok) throw new Error("تعذر تحميل مصادر الفيديو");
  return resp.json();
}

export async function testVideoSource(
  sourceId: string,
): Promise<{ status: string; success?: boolean; message: string }> {
  const resp = await fetch(`${API_BASE}/api/video-sources/${encodeURIComponent(sourceId)}/test`, {
    method: "POST",
    credentials: "include",
    headers: deviceHeaders(),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "فشل اختبار مصدر الفيديو");
  return data;
}


