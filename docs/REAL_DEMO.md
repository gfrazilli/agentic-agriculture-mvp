# Authorized real Sentinel demonstration

`prepare_real_demo` creates one confirmed field from a private local JSON file, runs the real
Sentinel pipeline synchronously, and emits a coordinate-redacted evidence manifest. Repeating the
same `demo_key` and zone count reuses the completed deterministic analysis instead of downloading
the imagery again.

The command deliberately does not accept coordinates directly on the command line, where shell
history and process listings could retain them. It accepts input only from:

- a JSON file outside this repository; or
- `.private-demo/*.json` inside this repository. The entire `.private-demo/` directory is ignored
  by Git.

Never add the private input, screenshots containing coordinates, or an unredacted Firestore record
to the repository.

## Private input

Create `.private-demo/authorized-field.json` locally. Replace every placeholder before running;
the block below is a shape reference and is not valid until real authorized values are supplied.

```json
{
  "schema_version": "1.0",
  "demo_key": "authorized-demo-2026",
  "name": "Authorized demonstration field",
  "crop": "soybean",
  "season_start": "YYYY-MM-DD",
  "season_end": "YYYY-MM-DD",
  "estimated_area_ha": 1.0,
  "reference_location": {
    "type": "Point",
    "coordinates": ["PRIVATE_LONGITUDE", "PRIVATE_LATITUDE"]
  },
  "boundary": {
    "type": "Polygon",
    "coordinates": [[
      ["PRIVATE_LONGITUDE", "PRIVATE_LATITUDE"],
      ["PRIVATE_LONGITUDE", "PRIVATE_LATITUDE"],
      ["PRIVATE_LONGITUDE", "PRIVATE_LATITUDE"],
      ["PRIVATE_LONGITUDE", "PRIVATE_LATITUDE"]
    ]]
  },
  "requested_zone_count": 4
}
```

The polygon must be valid and closed, and the reference point must lie inside it. `demo_key` is a
non-sensitive stable identifier of 8-64 lowercase letters, digits, hyphens, or underscores. If the
private input changes, choose a new key; the command refuses to silently replace a different field
that already uses the same key.

## Run locally with in-memory storage

This performs real Earth Search and COG reads but keeps the field, result, and artifacts only for
the lifetime of the command. It is useful for validation, not for a reusable judging cache.

PowerShell:

```powershell
$env:ANALYSIS_PIPELINE_BACKEND = "sentinel"
python manage.py prepare_real_demo `
  --input .private-demo/authorized-field.json `
  --confirm-authorized-data `
  --manifest .private-demo/real-demo-manifest.json
```

## Persist in Firestore and Cloud Storage

Authenticate with Application Default Credentials or execute the command under a service identity
that can read/write the configured Firestore database and artifact bucket. Configure:

```text
PERSISTENCE_BACKEND=firestore
ARTIFACT_BACKEND=gcs
ANALYSIS_PIPELINE_BACKEND=sentinel
GOOGLE_CLOUD_PROJECT=<project-id>
FIRESTORE_DATABASE=(default)
GCS_BUCKET=<private-artifact-bucket>
```

Then run the same command. The first successful execution downloads and processes real Sentinel-2
L2A observations. Later executions with the same input reuse the completed Firestore analysis.
Use `--reuse-only` before recording to prove the cache exists without making any external imagery
request:

```powershell
python manage.py prepare_real_demo `
  --input .private-demo/authorized-field.json `
  --confirm-authorized-data `
  --reuse-only `
  --manifest .private-demo/real-demo-manifest.json `
  --overwrite-manifest
```

## Manifest privacy boundary

The emitted manifest includes the analysis ID, real Sentinel scene IDs and dates, field and zone
indices, relative zone summaries, processing provenance, scope, and artifact URIs. It intentionally
omits:

- the reference location;
- the field boundary;
- every zone boundary;
- the private input path and authorization details;
- the private field name and `demo_key`.

Review the generated file before publishing it. Artifact URIs remain private Google Cloud Storage
references; sharing the manifest does not make the bucket public.
