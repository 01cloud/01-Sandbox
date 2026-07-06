# Summary of Changes - July 6, 2026

## 1. Repository Scanner UI Restructure
*   **Two-Column Layout**: Swapped the triple-panel layout for a cleaner two-column layout in `RepoScannerWidget.tsx`.
*   **Embedded Scan List**: Moved the repository scans list (`JobsPanel`) directly into the left sidebar column, sitting beneath the **Repository URL** inputs and clone credentials.
*   **Embedded Styles**: Configured `JobsPanel.tsx` with a new conditional `embedded` prop to seamlessly remove sidebar borders/padding and fit cleanly inside the left column.
*   **Restored Language Distribution Graph**: Resolved a caching bug in `useJobStore.ts` that caused the language distribution graph and per-language scans table to be missing. Now details are retrieved instantly on job completion and persist through page reloads via `localStorage` payload fallbacks.

## 2. Unit Testing & CI/CD Pipeline
*   **Config Unit Tests**: Added a beginner-friendly unit test suite (`tests/config/test_config.py`) to test default environment fallbacks, custom route prefixes, token headers, and JSON error handling inside `config.py` without touching database layers.
*   **CI Test Isolation**: Split the automated pytest execution in `.github/workflows/test-api.yaml` into four independent stages (PostgreSQL, Redis, Ratelimit, and Configuration) for easier tracking of failure sources.
*   **Warning Silence**: Muted pytest event-loop scope warnings by configuring `asyncio_default_fixture_loop_scope = function` inside `pytest.ini`.

## 3. Configuration Flow Documentation
*   **Architecture Header**: Added a descriptive module header comment block to the top of `config.py` mapping out which parts of the application (JWT validators, API Key routers, sandbox file scanners) import and depend on specific configuration endpoints.
