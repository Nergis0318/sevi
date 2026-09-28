import uuid

from app.paths import MediaPaths


def test_paths_are_uuid_scoped(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    assert p.job_dir(jid) == tmp_path / str(jid)
    assert p.source(jid).name == "source.bin"
    assert p.result(jid).name == "result.webm"
    assert p.source(jid).parent == p.job_dir(jid)


def test_source_name_is_not_user_controlled(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    assert p.source(jid).suffix == ".bin"


def test_prepare_creates_dir(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    d = p.prepare(jid)
    assert d.is_dir()
    assert p.source(jid).parent == d


def test_remove_job(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    p.prepare(jid)
    p.source(jid).write_bytes(b"x")
    p.remove_job(jid)
    assert not p.job_dir(jid).exists()


def test_remove_job_is_idempotent(tmp_path):
    p = MediaPaths(tmp_path)
    jid = uuid.uuid4()
    p.prepare(jid)
    p.remove_job(jid)
    p.remove_job(jid)  # 두 번째 호출은 조용히 성공
