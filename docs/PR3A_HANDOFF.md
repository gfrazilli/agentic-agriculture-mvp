# PR3A handoff: farmer interface

PR3A is a beginner-friendly, presentation-quality web interface built on top of the stable
PR2 API. It must not implement Sentinel processing, Gemini, ADK, MCP, or Google Cloud setup.
Those integrations belong to later PRs.

## Product flow

Build a mobile-first guided flow for a small farmer:

1. Sign in with the existing demonstration account.
2. Allow browser geolocation, or enter latitude and longitude manually.
3. Enter field name, crop, season dates, and estimated cultivated area.
4. Create the field through `POST /api/v1/fields/`.
5. Request a proposed polygon through
   `POST /api/v1/fields/{field_id}/boundary-suggestions/`.
6. Display the polygon on a map and let the farmer move vertices.
7. Require an explicit confirmation before saving `boundary_confirmed: true` through
   `PATCH /api/v1/fields/{field_id}/`.
8. Queue an analysis through `POST /api/v1/analyses/`.
9. Demonstrate the running and completed screens using the stable fixtures while the real
   processing pipeline is not yet part of the repository.
10. On the result screen, show four relative development zones, their area percentages,
    spectral summaries, scene dates, cloud coverage, and a clear non-diagnostic explanation.

The interface should say that zones developed differently from one another. It must not
claim to diagnose pests, soil, water stress, disease, or treatment.

## Stable fixture API

Use these authenticated routes during interface development:

- `GET /api/v1/fixtures/`
- `GET /api/v1/fixtures/field-draft/`
- `GET /api/v1/fixtures/boundary-suggestion/`
- `GET /api/v1/fixtures/analysis-running/`
- `GET /api/v1/fixtures/analysis-result/`

Every API response uses a versioned envelope with `schema_version: "1.0"`. Treat fixture
payloads as read-only contracts. Do not rename fields or edit fixture JSON in PR3A.

## Write request rules

All API routes require the existing signed-cookie login. Every `POST` must include:

```text
Content-Type: application/json
X-CSRFToken: <value of the csrftoken cookie>
Idempotency-Key: <new UUID generated once for this user action>
```

Keep the same idempotency key when retrying the exact same action. Generate another key
when the body or action changes. `PATCH` needs the CSRF token but does not require an
idempotency key.

Example field body:

```json
{
  "name": "Talhão da sede",
  "crop": "soja",
  "season_start": "2025-10-15",
  "season_end": "2026-03-10",
  "estimated_area_ha": 8.5,
  "reference_location": {
    "type": "Point",
    "coordinates": [-48.879, -23.982]
  }
}
```

The coordinate order is GeoJSON: longitude first, latitude second. The API accepts fields
up to 500 hectares. The polygon must be a closed, non-self-intersecting GeoJSON ring with no
more than 200 positions. Analysis and regrouping requests share a limit of three actions per
browser actor per day.

## Files that PR3A may change

Prefer changes limited to:

- `templates/`
- `core/static/`
- a small UI-focused module under `core/` if necessary
- UI tests under `tests/`
- translation entries under `locale/`

Do not change `agriculture/schemas/`, `agriculture/ports/`, `agriculture/adapters/`, fixture
JSON, persistence settings, or API semantics. If the UI exposes a real contract gap, describe
it in the PR instead of silently changing the backend.

Use a lightweight browser map library from a pinned, integrity-checked source, or vendor the
asset locally. Keep the screen usable without geolocation permission by offering manual
coordinates. Avoid adding a JavaScript build tool unless it materially improves the result.

## Suggested screens

- field onboarding form;
- proposed-boundary map with edit and confirmation controls;
- queued/running progress screen;
- completed result map with a four-color relative-zone legend;
- accessible textual zone summary below the map;
- friendly error states for 401, 409, 422, 429, and unavailable geolocation.

PT-BR is the primary experience. Preserve the existing English option. Use large touch
targets, concise rural language, high contrast, and a layout that works at 360 px width.

## Acceptance criteria

- Existing login, logout, language switch, health probes, and API tests remain green.
- A farmer can complete the entire guided flow on a phone-sized viewport.
- Geolocation denial does not block the flow.
- The proposed polygon can be inspected, adjusted, and explicitly confirmed.
- Duplicate clicks do not create duplicate resources.
- Loading, empty, validation, quota, and server-error states are visible and understandable.
- The running and result fixture screens render without changing fixture contracts.
- No screen presents relative development differences as an agronomic diagnosis.
- `ruff check .`, `ruff format --check .`, `pytest`, and `python manage.py check` pass.
- The Docker image still builds successfully.
