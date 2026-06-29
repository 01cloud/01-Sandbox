import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from scan_repository.models import DetectionTool, LanguageScanResult
from scan_repository.scan_repository import _run_scan_pipeline


@pytest.mark.asyncio
async def test_language_scan_concurrency():
    # Mock app_state
    app_state = MagicMock()

    mock_job = MagicMock()
    mock_job.metadata = {}

    app_state.job_tracker = MagicMock()
    app_state.job_tracker.get_job = MagicMock(return_value=mock_job)
    app_state.job_tracker.push_event = AsyncMock()

    # Mock dependencies
    async def mock_validate(*args, **kwargs):
        pass

    async def mock_clone(*args, **kwargs):
        return True, ""

    async def mock_provision(*args, **kwargs):
        return "mock_sandbox_id"

    async def mock_destroy(*args, **kwargs):
        pass

    async def mock_detect(*args, **kwargs):
        return {
            "Python": ["/tmp/a.py"],
            "Go": ["/tmp/b.go"],
            "Shell": ["/tmp/c.sh"],
        }, DetectionTool.TOKEI

    async def mock_scan(sandbox_id, language, files, percentage, *args, **kwargs):
        await asyncio.sleep(0.2)
        return LanguageScanResult(
            language=language,
            file_count=len(files),
            lines_of_code=100,
            percentage=percentage,
            findings=[],
        )

    # Apply patches and measure execution time
    with patch(
        "scan_repository.scan_repository.validate_github_repo",
        side_effect=mock_validate,
    ), patch(
        "scan_repository.scan_repository.clone_repo", side_effect=mock_clone
    ), patch(
        "scan_repository.scan_repository.provision_sandbox", side_effect=mock_provision
    ), patch(
        "scan_repository.scan_repository.destroy_sandbox", side_effect=mock_destroy
    ), patch(
        "scan_repository.scan_repository.detect_languages", side_effect=mock_detect
    ), patch(
        "scan_repository.scan_repository.scan_language", side_effect=mock_scan
    ):
        t0 = time.monotonic()
        await _run_scan_pipeline(
            job_id="test_job_id",
            repo_url="https://github.com/test/repo",
            owner="test",
            repo="repo",
            app_state=app_state,
        )
        elapsed = time.monotonic() - t0

        print(f"Elapsed time for 3 mock language scans (0.2s each): {elapsed:.2f}s")
        # Since they run concurrently, total time should be close to 0.2s, definitely < 0.45s.
        # Sequentially it would be 3 * 0.2 = 0.6s.
        assert elapsed < 0.45, f"Scans did not run concurrently. Took {elapsed:.2f}s"
