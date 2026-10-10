import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from quant_studio import QuantStudioError
from quant_studio.operations import backup_state, verify_backup


def test_backup_hash_restore_excludes_credentials_and_detects_damage(tmp_path):
    state = tmp_path / "state"
    (state / "runs/.recipes/abc").mkdir(parents=True)
    (state / "runs/.recipes/abc/HEAD").write_text("version")
    (state / "private").mkdir()
    (state / "private/password.txt").write_text("DO NOT ARCHIVE")
    (state / "studio-settings.json").write_text("{}")
    receipt = backup_state(state)
    archive = Path(receipt["archive"])
    with ZipFile(archive) as zipped:
        assert not any("private" in name for name in zipped.namelist())
    assert verify_backup(archive)["files"] == 2
    with ZipFile(archive, "a") as zipped:
        zipped.writestr("extra.txt", "unlisted")
    with pytest.raises(QuantStudioError, match="清单"):
        verify_backup(archive)


def test_backup_defers_when_jobs_are_active(tmp_path):
    state = tmp_path / "state"
    jobs = state / "runs/.jobs"
    jobs.mkdir(parents=True)
    (jobs / "abc.json").write_text(json.dumps({"status": "running"}))
    with pytest.raises(QuantStudioError, match="任务"):
        backup_state(state)
