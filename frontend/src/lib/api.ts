export type ProfileOption = { name: string; label: string };

export type ConfigOut = {
  max_upload_bytes: number;
  target_bytes: number;
  max_duration_s: number;
  profiles: ProfileOption[];
  default_profile: string;
};

export type JobStatusOut = {
  id: string;
  status:
    | "queued"
    | "probing"
    | "encoding"
    | "done"
    | "done_oversize"
    | "failed"
    | "cancelled";
  attempt: number;
  progress: number;
  source_name: string;
  source_bytes: number | null;
  result_bytes: number | null;
  duration_s: number | null;
  error: string | null;
  expires_at: string;
};

export type JobCreated = { id: string; status: string };

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `요청에 실패했습니다 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export function getConfig(): Promise<ConfigOut> {
  return fetch("/api/config").then(json<ConfigOut>);
}

export function getJob(id: string): Promise<JobStatusOut> {
  return fetch(`/api/jobs/${id}`).then(json<JobStatusOut>);
}

export function deleteJob(id: string): Promise<void> {
  return fetch(`/api/jobs/${id}`, { method: "DELETE" }).then((r) => {
    if (!r.ok && r.status !== 204) throw new Error("삭제에 실패했습니다.");
  });
}

export function downloadUrl(id: string): string {
  return `/api/jobs/${id}/download`;
}

export function uploadJob(
  file: File,
  profile: string,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<JobCreated> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    form.append("profile", profile);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/jobs");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (signal?.aborted) {
        reject(new DOMException("업로드가 중단되었습니다.", "AbortError"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as JobCreated);
      } else {
        const detail = (xhr.response as { detail?: string } | null)?.detail;
        reject(new Error(detail || `업로드에 실패했습니다 (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new Error("네트워크 오류가 발생했습니다."));
    xhr.onabort = () =>
      reject(new DOMException("업로드가 중단되었습니다.", "AbortError"));
    if (signal)
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    if (signal?.aborted) {
      xhr.abort();
      return;
    }
    xhr.send(form);
  });
}
