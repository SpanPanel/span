# Integration Panel Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register a sidebar panel in the SPAN Panel integration that serves the span-card's panel JS bundle, giving users a full-page dashboard with monitoring
and configuration.

**Architecture:** Add git submodule for span-card dist output, register a custom panel via `panel_custom` in `async_setup`, serve the JS bundle as a static
path. Minimal Python changes.

**Tech Stack:** Python, Home Assistant custom integration APIs

**Repo:** `/Users/bflood/projects/HA/span` (branch: `2.0.5`)

**Tests:** `.venv/bin/python -m pytest tests/ -q`

---

## Task 1: Add span-card as git submodule

**Files:**

- Create: `custom_components/span_panel/frontend/` (submodule)
- Modify: `.gitmodules`

- [ ] **Step 1: Add the submodule**

```bash
cd /Users/bflood/projects/HA/span
git submodule add \
  -b integration-panel \
  https://github.com/SpanPanel/span-card.git \
  custom_components/span_panel/frontend
```

This creates `.gitmodules` and checks out the span-card repo into `custom_components/span_panel/frontend/`.

- [ ] **Step 2: Verify the dist file exists**

```bash
ls custom_components/span_panel/frontend/dist/span-panel.js
```

Expected: File exists (after span-card plan is complete and built).

If the file does not exist yet (span-card work is in progress), create a placeholder:

```bash
mkdir -p custom_components/span_panel/frontend/dist
echo "// placeholder — built by span-card" > custom_components/span_panel/frontend/dist/span-panel.js
```

- [ ] **Step 3: Add frontend dist to .gitignore exclusion**

Ensure the submodule's dist files are not ignored by the integration repo's `.gitignore`. Check if `dist/` is in `.gitignore`:

```bash
grep -n "dist" .gitignore
```

If `dist/` is listed, add an exception:

```text
!custom_components/span_panel/frontend/dist/
```

- [ ] **Step 4: Commit**

```bash
cd /Users/bflood/projects/HA/span
git add .gitmodules custom_components/span_panel/frontend
git commit -m "chore: add span-card as git submodule for panel frontend"
```

---

## Task 2: Register panel in async_setup

**Files:**

- Modify: `custom_components/span_panel/__init__.py`
- Test: `tests/test_panel_registration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_panel_registration.py
"""Tests for integration panel registration."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestPanelRegistration:
    """Tests for sidebar panel registration."""

    @pytest.mark.asyncio
    async def test_panel_registered_on_setup(self):
        """Panel is registered when async_setup runs."""
        from custom_components.span_panel import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.register_static_path = MagicMock()

        mock_panel_custom = MagicMock()
        mock_panel_custom.async_register_panel = AsyncMock()
        hass.components = MagicMock()
        hass.components.panel_custom = mock_panel_custom

        hass.config = MagicMock()
        hass.config.path = MagicMock(return_value="/fake/path/span-panel.js")

        config = {}
        result = await async_setup(hass, config)

        assert result is True
        hass.http.register_static_path.assert_called_once()
        mock_panel_custom.async_register_panel.assert_called_once()

        call_kwargs = mock_panel_custom.async_register_panel.call_args
        # Positional arg 0 is hass
        assert call_kwargs[1]["sidebar_title"] == "Span Panel"
        assert call_kwargs[1]["sidebar_icon"] == "mdi:lightning-bolt"

    @pytest.mark.asyncio
    async def test_panel_static_path_points_to_frontend(self):
        """Static path serves the frontend JS bundle."""
        from custom_components.span_panel import async_setup

        hass = MagicMock()
        hass.data = {}
        hass.http = MagicMock()
        hass.http.register_static_path = MagicMock()
        hass.components = MagicMock()
        hass.components.panel_custom = MagicMock()
        hass.components.panel_custom.async_register_panel = AsyncMock()
        hass.config = MagicMock()
        hass.config.path = MagicMock(
            return_value="/config/custom_components/span_panel/frontend/dist"
        )

        await async_setup(hass, {})

        static_call = hass.http.register_static_path.call_args
        assert "/span_panel_frontend" in static_call[0][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_panel_registration.py -v`

Expected: FAIL — `async_setup` either doesn't exist or doesn't register the panel yet.

- [ ] **Step 3: Implement panel registration**

In `custom_components/span_panel/__init__.py`, add or modify `async_setup`:

```python
import os

PANEL_URL = "/span_panel_frontend"
PANEL_FRONTEND_DIR = os.path.join(
    os.path.dirname(__file__), "frontend", "dist"
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the SPAN Panel integration (domain-level)."""
    hass.data.setdefault(DOMAIN, {})

    # Register sidebar panel serving the frontend JS bundle
    hass.http.register_static_path(
        PANEL_URL,
        PANEL_FRONTEND_DIR,
        cache_headers=True,
    )
    await hass.components.panel_custom.async_register_panel(
        hass,
        webcomponent_name="span-panel",
        frontend_url_path="span-panel",
        sidebar_title="Span Panel",
        sidebar_icon="mdi:lightning-bolt",
        module_url=f"{PANEL_URL}/span-panel.js",
        require_admin=False,
        config={},
    )

    return True
```

**Note:** If `async_setup` already exists in `__init__.py`, add the panel registration code to it. Do not replace existing setup logic.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_panel_registration.py -v`

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`

Expected: All tests pass (no regressions).

- [ ] **Step 6: Commit**

```bash
cd /Users/bflood/projects/HA/span
git add custom_components/span_panel/__init__.py \
  tests/test_panel_registration.py
git commit -m "feat: register sidebar panel serving frontend JS bundle"
```

---

## Task 3: Add frontend to manifest and HACS config

**Files:**

- Modify: `custom_components/span_panel/manifest.json`
- Modify: `hacs.json` (if applicable)

- [ ] **Step 1: Check if manifest needs panel flag**

Read `custom_components/span_panel/manifest.json` and check if a `panel` or `frontend` field is needed. HA custom integrations that register panels don't
typically need special manifest fields, but verify.

- [ ] **Step 2: Ensure HACS config includes frontend dir**

If HACS is used for distribution, verify that `hacs.json` or the repo structure will include the `frontend/dist/` directory in releases.

- [ ] **Step 3: Commit if changes needed**

```bash
cd /Users/bflood/projects/HA/span
git add custom_components/span_panel/manifest.json
git commit -m "chore: update manifest for panel frontend"
```

---

## Task 4: End-to-end verification

- [ ] **Step 1: Build span-card**

```bash
cd /Users/bflood/projects/HA/cards/span-card
npm run build
ls dist/span-panel.js dist/span-panel-card.js
```

Both files should exist.

- [ ] **Step 2: Update submodule in integration**

```bash
cd /Users/bflood/projects/HA/span
git submodule update --remote custom_components/span_panel/frontend
```

- [ ] **Step 3: Run integration tests**

```bash
cd /Users/bflood/projects/HA/span
.venv/bin/python -m pytest tests/ -q
```

Expected: All tests pass.

- [ ] **Step 4: Manual smoke test**

Restart Home Assistant. Verify:

1. "Span Panel" appears in the sidebar
2. Clicking it opens the full-page panel
3. The Panel tab shows the physical breaker-box view
4. The Monitoring tab shows monitoring status or "not enabled" message
5. The Settings tab shows a link to the integration config
