# Publishing copy

## YouTube title

1415 Agri — Gemini-Powered Satellite Scouting for Farmers | Hackathon Demo

## YouTube description

1415 Agri turns real Sentinel-2 multispectral observations into an understandable and accountable field-scouting priority map.

In this end-to-end demonstration, a farmer confirms a field boundary, a private Google Cloud worker processes real Sentinel-2 L2A observations and deterministic NDVI, NDRE, and NDMI measurements, and Gemini 3.5 Flash coordinates specialist agents through Google ADK and MCP tools. Gemini then explains which area to scout first using persisted evidence and, with explicit permission, safely queues a new comparison without duplicating retries.

This is a map for investigation, not a diagnosis. The satellite measures, Gemini reasons over typed evidence, and the farmer decides what to inspect in the field.

Built for the All Things Agentic Hackathon.

- Live product: https://1415agri.com/
- Portuguese: https://1415agri.com/pt/
- Source code: https://github.com/gfrazilli/agentic-agriculture-mvp

Stack: Gemini 3.5 Flash on Vertex AI, Google ADK, MCP, Cloud Run, Cloud Tasks, Firestore, Cloud Storage, and Sentinel-2 L2A imagery via Earth Search/AWS Open Data.

#Gemini #GoogleCloud #AgenticAI #PrecisionAgriculture #Sentinel2 #Hackathon

## Short public build post

We built 1415 Agri for the All Things Agentic Hackathon: a collaborative field-scouting partner for farmers who are often left out of precision agriculture.

The demo uses real Sentinel-2 L2A observations. Deterministic services calculate NDVI, NDRE, and NDMI; Gemini 3.5 Flash coordinates specialist agents through Google ADK and MCP; and every explanation remains connected to persisted dates, indices, bands, and provenance.

The most important design boundary is simple: the satellite measures, Gemini reasons, and the farmer decides. 1415 Agri highlights where crop development differed and helps plan the next field visit without pretending to diagnose a cause from space.

The full workflow runs on Google Cloud as separately permissioned web, worker, MCP, and agent services.

Demo: https://1415agri.com/
Code: https://github.com/gfrazilli/agentic-agriculture-mvp

#AllThingsAgentic #Gemini #GoogleCloud #AgenticAI #Agriculture

## Upload checklist

- Upload `1415-agri-hackathon.mp4`.
- Upload `1415-agri-hackathon.srt` as English captions even though captions are also burned in.
- Use the title and description above.
- Set visibility to Public.
- Do not mark as made for kids.
- Wait for 1080p processing to finish.
- Test the public link in a signed-out window.
- Only then copy the URL into the Devpost draft; do not submit Devpost without explicit approval.
