import { downloadUrl, type JobStatusOut } from "../lib/api";

const LABEL: Record<JobStatusOut["status"], string> = {
  queued: "대기 중",
  probing: "파일 분석 중",
  encoding: "인코딩 중",
  done: "완료",
  done_oversize: "완료 (목표 초과)",
  failed: "실패",
  cancelled: "취소됨",
};

function mb(bytes: number | null): string {
  if (bytes == null) return "-";
  return `${(bytes / 1_000_000).toFixed(1)}MB`;
}

export function JobStatus({ job }: { job: JobStatusOut }) {
  const pct = Math.round(job.progress * 100);
  const saved =
    job.source_bytes && job.result_bytes != null
      ? Math.max(0, 1 - job.result_bytes / job.source_bytes)
      : null;

  return (
    <div className="card">
      <div className="row">
        <span>{LABEL[job.status]}</span>
        <span className="muted">
          시도 {job.attempt} · {pct}%
        </span>
      </div>
      <div className="bar">
        <i style={{ width: `${pct}%` }} />
      </div>

      {job.result_bytes != null && (
        <div className="row">
          <span className="muted">원본 → 결과</span>
          <span>
            {mb(job.source_bytes)} → {mb(job.result_bytes)}
            {saved != null && ` (${(saved * 100).toFixed(0)}% 절감)`}
          </span>
        </div>
      )}

      {job.error && <p className="err">{job.error}</p>}

      {(job.status === "done" || job.status === "done_oversize") && (
        <p>
          <a href={downloadUrl(job.id)}>
            <button type="button">다운로드</button>
          </a>
        </p>
      )}
    </div>
  );
}
