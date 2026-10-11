"""Exercise registry preservation and failed visibility without touching real Run."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows registry integration")
def test_startup_preserves_other_entries_and_requires_system_visibility(tmp_path):
    installer = Path(__file__).parents[1] / "tools/Install-UserStartup.ps1"
    startup = tmp_path / "startup.ps1"
    startup.write_text("throw 'The installer must never execute this file'\n")
    harness = Path(__file__).parent / "fixtures/windows_user_startup.ps1"
    shell = (
        Path(os.environ["SYSTEMROOT"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(
        [
            str(shell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-Installer",
            str(installer),
            "-StartupScript",
            str(startup),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STARTUP_REGISTRY_CHECKS_PASSED" in result.stdout
