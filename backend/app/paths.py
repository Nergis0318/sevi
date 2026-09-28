import shutil
import uuid
from pathlib import Path


class MediaPaths:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def job_dir(self, job_id: uuid.UUID) -> Path:
        return self.root / str(job_id)

    def source(self, job_id: uuid.UUID) -> Path:
        return self.job_dir(job_id) / "source.bin"

    def result(self, job_id: uuid.UUID) -> Path:
        return self.job_dir(job_id) / "result.webm"

    def prepare(self, job_id: uuid.UUID) -> Path:
        d = self.job_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def remove_job(self, job_id: uuid.UUID) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
