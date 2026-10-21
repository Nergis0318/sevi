import { useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { DropZone } from "../components/DropZone";
import { JobStatus } from "../components/JobStatus";
import {
  ApiError,
  deleteJob,
  getConfig,
  getJob,
  uploadJob,
  type JobStatusOut,
} from "../lib/api";

function jobFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get("job");
}

function mb(bytes: number): string {
  return `${(bytes / 1_000_000).toFixed(0)}MB`;
}

export function App() {
  const { data: config } = useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
  });
  const [phase, setPhase] = useState<"idle" | "upload" | "job">(() =>
    jobFromUrl() ? "job" : "idle",
  );
  const [uploadPct, setUploadPct] = useState(0);
  const [jobId, setJobId] = useState<string | null>(() => jobFromUrl());
  const [job, setJob] = useState<JobStatusOut | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warn, setWarn] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  const pollFails = useRef(0);
  const uploadAbort = useRef<AbortController | null>(null);

  const stopPolling = () => {
    if (timer.current != null) {
      window.clearInterval(timer.current);
      timer.current = null;
    }
  };

  useEffect(() => stopPolling, []);

  useEffect(() => {
    if (!jobId) return;
    stopPolling();
    pollFails.current = 0;
    timer.current = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        pollFails.current = 0;
        setWarn(null);
        setJob(j);
        if (
          j.status === "done" ||
          j.status === "done_oversize" ||
          j.status === "failed" ||
          j.status === "cancelled"
        ) {
          stopPolling();
        }
      } catch (e) {
        if (e instanceof ApiError && e.status < 500 && e.status !== 429) {
          reset();
          setError(
            e.status === 404
              ? "작업을 찾을 수 없습니다. 만료되었거나 삭제된 작업입니다."
              : e.message,
          );
          return;
        }
        pollFails.current += 1;
        if (pollFails.current >= 5) {
          setError(e instanceof Error ? e.message : String(e));
          stopPolling();
        } else {
          setWarn(
            `상태 확인에 실패했습니다. 재시도 중… (${pollFails.current}/5)`,
          );
        }
      }
    }, 2000);
    return stopPolling;
  }, [jobId]);

  const selectedProfile = profile ?? config?.default_profile ?? "balanced";

  const onFile = useCallback(
    async (file: File) => {
      setError(null);
      if (config && file.size > config.max_upload_bytes) {
        setError(`업로드 한도(${mb(config.max_upload_bytes)})를 초과했습니다.`);
        return;
      }
      setPhase("upload");
      setUploadPct(0);
      setJob(null);
      setJobId(null);
      const ac = new AbortController();
      uploadAbort.current = ac;
      try {
        const created = await uploadJob(
          file,
          selectedProfile,
          setUploadPct,
          ac.signal,
        );
        if (ac.signal.aborted) return;
        window.history.pushState(null, "", `/?job=${created.id}`);
        setJobId(created.id);
        setPhase("job");
      } catch (e) {
        if (ac.signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
        setPhase("idle");
      }
    },
    [config, selectedProfile],
  );

  const resetState = () => {
    uploadAbort.current?.abort();
    uploadAbort.current = null;
    stopPolling();
    setPhase("idle");
    setJob(null);
    setJobId(null);
    setError(null);
    setWarn(null);
    setUploadPct(0);
  };

  const reset = () => {
    resetState();
    // 삭제/실패로 떠나는 자리라 replace — push 하면 뒤로가기가 죽은 작업 URL 로 되돌아간다.
    window.history.replaceState(null, "", "/");
  };

  useEffect(() => {
    const onPop = () => {
      uploadAbort.current?.abort();
      uploadAbort.current = null;
      const id = jobFromUrl();
      if (id) {
        if (id !== jobId) {
          setJob(null);
          setJobId(id);
        }
        setPhase("job");
        setError(null);
        setWarn(null);
      } else {
        resetState();
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [jobId]);

  const remove = async () => {
    if (jobId) await deleteJob(jobId).catch(() => undefined);
    reset();
  };

  return (
    <>
      <h1>sevi</h1>
      <p className="tagline muted">
        영상을 49.9MB 이하 .webm (VP9 + Opus) 으로 압축합니다
        {config && ` · 최대 업로드 ${mb(config.max_upload_bytes)}`}
      </p>

      {phase === "idle" && (
        <div className="card">
          <p>
            <label>
              프로필{" "}
              <select
                value={selectedProfile}
                onChange={(e) => setProfile(e.target.value)}
              >
                {(config?.profiles ?? []).map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
          </p>
          <DropZone disabled={false} onFile={onFile} />
        </div>
      )}

      {phase === "upload" && (
        <div className="card">
          <div className="row">
            <span>업로드 중</span>
            <span className="muted">{Math.round(uploadPct * 100)}%</span>
          </div>
          <div className="bar">
            <i style={{ width: `${uploadPct * 100}%` }} />
          </div>
          <div className="card-foot">
            <button type="button" onClick={remove}>
              업로드 취소
            </button>
          </div>
        </div>
      )}

      {phase === "job" && job && <JobStatus job={job} onRemove={remove} />}
      {phase === "job" && !job && (
        <div className="card">
          <p className="muted">작업 정보를 불러오는 중…</p>
          <div className="card-foot">
            <button type="button" onClick={remove}>
              새로 시작
            </button>
          </div>
        </div>
      )}

      {error && <p className="err">{error}</p>}
      {warn && <p className="muted">{warn}</p>}
    </>
  );
}
