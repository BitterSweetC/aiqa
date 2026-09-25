"""Deterministic file download verification for AIQA."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from aiqa.models.test_case import Expectation, VerificationResult

logger = logging.getLogger(__name__)


class DownloadVerifier:
    """Verifies that a browser file download completed and matches expected filename/size."""

    async def verify(
        self,
        expectation: Expectation,
        session: Any = None,
        page: Any = None,
        **_kwargs: Any,
    ) -> VerificationResult:
        if not isinstance(expectation, Expectation) and isinstance(session, Expectation):
            expectation, session = session, expectation

        downloads: list[dict[str, Any]] = []
        if session is not None and hasattr(session, "downloads"):
            downloads = list(getattr(session, "downloads", []))
        elif page is not None and hasattr(page, "_aiqa_downloads"):
            downloads = list(getattr(page, "_aiqa_downloads", []))

        if not downloads:
            return VerificationResult(
                expectation=expectation,
                passed=False,
                actual_value="0 downloads",
                message="Expected a file download, but no downloads were captured during the session",
            )

        target_val = (expectation.value or "").strip().lower()
        for dl in downloads:
            filename = str(dl.get("suggested_filename", "")).strip()
            size_bytes = int(dl.get("size_bytes", 0))
            if size_bytes <= 0 and dl.get("_download") is not None:
                try:
                    dl_path = await dl["_download"].path()
                    if dl_path:
                        p = Path(dl_path)
                        dl["path"] = str(p)
                        if p.exists():
                            size_bytes = p.stat().st_size
                            dl["size_bytes"] = size_bytes
                except Exception as err:  # noqa: BLE001
                    logger.debug("Could not resolve download path for '%s': %s", filename, err)
            if size_bytes <= 0:
                continue
            if not target_val or target_val in filename.lower():
                return VerificationResult(
                    expectation=expectation,
                    passed=True,
                    actual_value=f"{filename} ({size_bytes} bytes)",
                    message=f"Verified downloaded file '{filename}' ({size_bytes} bytes)",
                )

        observed = ", ".join(
            f"{d.get('suggested_filename', 'unknown')} ({d.get('size_bytes', 0)}B)"
            for d in downloads
        )
        return VerificationResult(
            expectation=expectation,
            passed=False,
            actual_value=observed,
            message=(
                f"Downloaded file(s) [{observed}] did not match expected "
                f"non-empty download '{expectation.value}'"
            ),
        )


__all__ = ["DownloadVerifier"]
