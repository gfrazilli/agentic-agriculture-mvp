# Final submission checklist

The official deadline is **August 31, 2026 at 5:00 PM Pacific Daylight Time**. That corresponds
to **9:00 PM in Brasília on August 31**. Treat 8:00 PM Brasília time as the internal stop: uploads,
team invitations, and the Devpost submit action should already be complete.

## 1. Eligibility and team

- [ ] Every entrant is above the age of majority in their jurisdiction and otherwise eligible under
  the official rules.
- [ ] Every contributor who should be on the team has a Devpost account, has been invited to the
  project, and has accepted before submission.
- [ ] One eligible individual is named as the Representative authorized to submit for the team and
  receive any prize on its behalf.
- [ ] The Devpost project selects exactly one core category: **The Collaborative Partner**.
- [ ] If Startup Excellence is selected in addition to the core track, the submitting organization is
  incorporated and a corporate email address is supplied. Do not select it merely because the entrant
  is a business owner.
- [ ] Team members understand that one project can receive at most one prize.

## 2. Project-period, ownership, and data rights

- [ ] Repository history shows that the submitted work was created during August 3–31, 2026.
- [ ] The submission discloses standard open-source dependencies, public data services, and use of AI
  coding assistants.
- [ ] Any other pre-existing work is explicitly disclosed. Do not incorporate undisclosed code,
  screenshots, research results, or assets created before the submission period.
- [ ] The demonstration field is `[AUTHORIZED_FIELD_NAME]`, and the team has permission to use its
  geometry and imagery in a public video.
- [ ] The unrelated TCC field, private data, screenshots, maps, and results are absent from the
  submission.
- [ ] Earth Search and Sentinel-2/AWS Open Data use complies with their access and attribution terms.
- [ ] Every image, icon, font, recording, logo, and audio element is owned, licensed, or permitted.
- [ ] No material is unlawful, defamatory, discriminatory, threatening, offensive, or otherwise
  inconsistent with the contest rules and spirit.
- [ ] Third-party marks appear only where necessary to identify an actual integration or data source;
  no advertising, slogan, or visual treatment implies an endorsement that does not exist.
- [ ] The submission does not expose personal data, unnecessary exact coordinates, credentials, or
  other information outside the field owner's authorization.

## 3. Mandatory technology baseline

- [ ] The final deployed agent uses **Gemini 3.5 or newer** through Vertex AI or the Gemini API.
- [ ] The submitted build uses at least one eligible Google agent framework; this project uses
  **Google ADK**.
- [ ] The submitted build uses Google Cloud infrastructure; this project uses Cloud Run, Firestore,
  Cloud Storage, Cloud Tasks, Secret Manager, Artifact Registry, Cloud Build, and Vertex AI.
- [ ] The final configuration and video visibly show the real model name and do not rely on a fixture
  or mocked Gemini response.
- [ ] The final public build supports English use.

## 4. Product and claim audit

- [ ] A field cannot be analyzed until its proposed boundary is explicitly confirmed.
- [ ] The real-data demonstration has **Guided demonstration** disabled.
- [ ] Every displayed scene identifier, date, spectral value, zone, geometry, and provenance record
  comes from the same persisted run.
- [ ] The final run record is preserved so the video, Devpost description, and repository evidence can
  be cross-checked.
- [ ] The result is described as relative-development differences within one field, not a diagnosis.
- [ ] No claim identifies pests, disease, soil condition, water stress, irrigation need, treatment,
  yield, or agronomic cause from the satellite result.
- [ ] No claim says that feedback retrains Gemini. The current product persists feedback against the
  analysis and session.
- [ ] The approximately 42-second, three-scene, two-zone observation remains labeled **local
  validation** unless the final deployment manifest independently confirms those values.
- [ ] The final text does not promise a fixed runtime, fixed scene count, or guaranteed number of
  zones.

## 5. Code and reproducibility

- [ ] Merge only reviewed pull requests intended for the submission.
- [ ] Confirm the default branch contains the exact revision used for the video and deployed services.
- [ ] Run:

  ```bash
  ruff check .
  ruff format --check .
  pytest -q
  python manage.py check
  docker build -t agentic-agriculture-submission .
  ```

- [ ] Run the production preflight with the deployed endpoints:

  ```bash
  python manage.py demo_preflight \
    --web-url="[FINAL_HOSTED_URL]" \
    --agent-url="$AGENT_API_URL" \
    --mcp-url="$AGENT_MCP_URL"
  ```

- [ ] Run `infra/gcp/smoke.sh` and retain its non-secret output as deployment evidence.
- [ ] Verify the root README contains complete local spin-up and cloud-deployment instructions.
- [ ] Verify the public repository is
  https://github.com/gfrazilli/agentic-agriculture-mvp and has no secrets, `.env`, password, token,
  service-account key, billing detail, or unauthorized field data in history.
- [ ] Confirm the architecture diagram renders correctly. Export the Mermaid diagram in
  `ARCHITECTURE_NARRATIVE.md` to a legible PNG or SVG for Devpost.

## 6. Hosted project and judge access

- [ ] Replace `[FINAL_HOSTED_URL]` everywhere with the exact stable URL submitted to Devpost.
- [ ] Verify `/live` returns a healthy response and `/ready` passes immediately before recording and
  submitting.
- [ ] Test the entire workflow from a clean, logged-out browser.
- [ ] Supply the current demo credentials in Devpost's private testing instructions; do not publish
  them in the code repository or video.
- [ ] Make judge access free of charge and without an avoidable geographic, account, or permission
  restriction through the judging period.
- [ ] Confirm unauthenticated users cannot invoke the worker, MCP, or ADK Cloud Run services.
- [ ] Confirm the public web gateway can invoke the private ADK service and that ADK can invoke MCP.
- [ ] Preserve one completed real run in Firestore and its artifacts in Cloud Storage for reliable
  evaluation.

## 7. Demo video

- [ ] Follow `VIDEO_SCRIPT.md` and finish at or before **4:00**; target 3:50–3:58.
- [ ] Include the problem, value proposition, and app in action.
- [ ] Include a continuous, normal-speed proof-of-action segment with a visible UI or persisted-state
  change.
- [ ] Show the `.run.app` URL and Google Cloud Console, Cloud Run dashboard, or relevant logs.
- [ ] Show a Gemini answer and the model/agent/tool trace.
- [ ] Show an agent-triggered mutation such as `request_field_analysis`, not only retrieval.
- [ ] Show real scene evidence and the non-diagnostic scope.
- [ ] Use English narration or accurate, synchronized English subtitles.
- [ ] Check the video for private coordinates, secrets, tokens, account email, billing information, and
  unrelated browser notifications.
- [ ] Upload to **YouTube or Vimeo** and set it to publicly visible, not private or unlisted.
- [ ] Let the platform finish HD processing, then watch the public version from beginning to end while
  logged out.
- [ ] Replace `[PUBLIC_VIDEO_URL]` everywhere with the final public URL.

## 8. Devpost fields and required materials

- [ ] Project name and tagline match `DEVPOST_SUBMISSION.md`.
- [ ] Category is **The Collaborative Partner**.
- [ ] Hosted-project URL is `[FINAL_HOSTED_URL]`.
- [ ] Public repository URL is
  [gfrazilli/agentic-agriculture-mvp](https://github.com/gfrazilli/agentic-agriculture-mvp).
- [ ] Public video URL is `[PUBLIC_VIDEO_URL]`.
- [ ] Text description includes features, functionality, technologies, other data sources, findings,
  and learnings.
- [ ] Testing instructions include the login procedure and explain how to run one real analysis.
- [ ] Architecture diagram clearly connects Gemini, ADK, MCP, frontend, worker, storage, database, and
  Google Cloud services.
- [ ] Screenshots are from the submitted build and do not imply a capability not present in the video
  or code.
- [ ] All submission material is in English or has a complete English translation.
- [ ] Search all five files in this directory for `[AUTHORIZED_FIELD_NAME]`, `[FINAL_HOSTED_URL]`, and
  `[PUBLIC_VIDEO_URL]`; replace every occurrence before submission.
- [ ] Save a Devpost draft, preview it while logged out where possible, then use the final submit
  action before the internal 8:00 PM Brasília cutoff.

## 9. Optional bonus contributions

These are optional. Do not risk the required submission to finish them.

- [ ] A public build article, podcast, or video accurately explains the project and explicitly states
  that it was created for the purpose of entering the All Things Agentic Hackathon.
- [ ] A public social post accurately presents the project. For X or LinkedIn, include
  `#AllThingsAgenticHackathon` exactly as specified in the official judging section.
- [ ] Any additional Google AI model is genuinely integrated and visibly functional. Naming or
  calling an unused model does not qualify.

The official rules award up to 0.2 points for qualifying public build content, up to 0.2 for a
qualifying social post, and 0.2 for each successfully integrated additional Google AI model, capped
at 0.6 model bonus points.

## 10. After submission

- [ ] Record the submission confirmation and timestamp.
- [ ] Do not edit the submitted Devpost materials after the deadline.
- [ ] Leave the submitted video, repository revision, hosted site, and other judged artifacts exactly
  as submitted until winners are announced. Continue development only in a separate fork or copy.
- [ ] Monitor the Representative's email. The rules can require a potential winner to respond quickly
  and complete eligibility verification.
- [ ] Keep the hosted project available for judging while controlling cost with the existing
  scale-to-zero limits and budget alerts.

## Official references

- [Official rules](https://allthingsagentichackathon.devpost.com/rules)
- [Official challenge overview](https://allthingsagentichackathon.devpost.com/)
- [Official FAQ](https://allthingsagentichackathon.devpost.com/details/faqs)
- [Official updates and submission reminders](https://allthingsagentichackathon.devpost.com/updates)
