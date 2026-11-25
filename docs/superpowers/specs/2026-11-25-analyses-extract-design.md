# Analyses: the Extract step — design

**Date:** 2026-11-25
**Status:** approved in conversation, 2026-11-25
**Extends:** `2026-11-22-analyses-design.md`

Items marked **(call)** were chosen by the designer and are open to correction.

## 1. Intent

Analysts describe their system in plain language. Turning that text into the
structured facts the research worked from (people, components, data flows,
sign-in, controls) is a task of its own. A new step, **Extract**, has a model do
it with the owner's extractor prompt; the analyst then approves, edits or
rejects every value before scenarios are generated.

```
(1) System ── (2) Extract ── (3) Generate ── (4) Label ── (5) Report
```

## 2. Decided with the owner

- Extract is a step of its own, between System and Generate.
- The extractor prompt is the owner's text (2026-11-25), unchanged except that
  `{{SYSTEM_DESCRIPTION}}` becomes the analyst's description.
- Recommended extractor model: `anthropic/claude-sonnet-5.5`.
- The extracted values are shown as separate fields, grouped by the prompt's
  sections, each approved, edited or rejected.
- The generator reads both: the approved fields written out as a structured
  description, then the analyst's original text. The generation and labelling
  prompts are unchanged; only the system description they receive is richer.
- "Try an example" fills in the owner's SmartStop text (Al Noor City, 2032).
- Buttons that move between steps say "Continue to …"; only buttons that run a
  model say "Generate…" or "Label…".
- The number of scenarios sits inside the Generate button:
  `[ Generate [ 8 ▾ ] scenarios ]`.

## 3. Behaviour

### Extraction

- One model call, run by the worker like generation, with progress and cancel.
- Settings panel as for the other steps: model (Sonnet 5.5 recommended),
  temperature (0.0 recommended **(call)**). The prompt is fixed and shown as
  "System description extractor v1 [Recommended]".
- The response must be the prompt's JSON shape. A reply that is not valid JSON
  or not that shape fails the run, with the reason, and changes nothing.
- "Extract again" replaces all items after a confirmation.
- Extraction records the description it read. If the description changes
  afterwards, the step says "The description changed since extraction" and
  Generate stays disabled until the analyst extracts again **(call)**.

### Checking each value

- Every value the server cannot accept is still shown, marked with the
  problem, and must be edited or rejected: a value outside the field's allowed
  list, a field the prompt does not define, or an id that names no element.
- Fields the prompt marks "Always Possible" are shown as Possible whatever the
  model returned **(call)**.
- Editing offers only valid choices: the field's allowed values; for a fact
  whose value is another element, the extracted elements; a number for
  `retention_days`; true or false for `removable_media`.
- Rejecting an element also excludes the facts and controls that refer to it;
  they show "refers to a rejected element" **(call)**.
- "Approve all Asserted" approves every pending Asserted value without a
  problem.
- Gaps (`not_assessable`, `no_term`) are listed for information; nothing to
  approve.
- Generate is enabled once an extraction exists, is current, and no value is
  left undecided.

### What the generator reads

`target_system.body` becomes:

```
## Structured description

### 1. People and organizations
- CitizenUsers (Citizen)
- MoveAILtd (PrivateSectorOrganization)
### 2. System components
- SmartStopApp (MobileApp) …
### 4. Data flows
- DF_CameraToCloud: BusStopCameras → MoveAICloud; data: BiometricData; boundary: ServiceToCloud
### 5. Sign-in and access …
### Facts
- StaffLogin requires_task CredentialEntry
- RoutePlanner automated_decision_about CitizenUsers …
### 8. Controls
- StaffLogin: MultiFactorAuthentication absent
### Not covered by the description
- Physical entry

## Original description

<the analyst's text>
```

Only approved values appear, without their fact type or reasons **(call)**.
The labeller receives the same body, so both models see the same system.

### Locking

- Extraction can run and be edited while the description is unlocked.
- Starting generation locks the description and the extraction together, and
  records the structured body used.

## 4. Data

- `AnalysisItem`: analysis, kind (element, fact, control), section, field,
  element id or subject id, value, state (controls), fact type, reason, other
  reading, problem, decision (pending, approved, rejected), order.
- Analysis gains: extraction settings, the gaps, and the description digest
  the extraction read. Runs gain the kind "extract". New columns are nullable
  or have database defaults, as before, so the code at `0620f6c` keeps working.
- Fields, allowed values and fact-type rules live in one Python module copied
  from the prompt's tables, used for checking and for the edit choices.

## 5. Report and PDF

Research details add the extraction setup (model, temperature, prompt, whether
Recommended) and a count of values approved, edited and rejected.

## 6. Not in scope

Adding values by hand; extracting from uploaded files; changing the
generation or labelling prompts.

## 7. Approaches

| | Approach | Trade-off |
|---|---|---|
| **A** | **A separate step with a stored, reviewable item per value** | **Chosen. Clear review, one run like the others** |
| B | Extract inside the System step | Fewer steps, but crowds the first screen and hides the review |
| C | Keep the extraction as one JSON document | Simpler storage, but item edits and checks become fiddly |

## 8. Settled after the builder check

- **Prompt.** `resources/presets/extractor_v1.txt`, the owner's text verbatim, sent
  as one user message with no system prompt **(call)**. `{{SYSTEM_DESCRIPTION}}`
  is a plain string replacement; no template engine touches the text.
- **Request schema.** A structural JSON schema (the four top-level keys, item
  objects with string fields), with no lists of allowed values, so out-of-list
  values reach the review marked rather than being blocked **(call)**.
- **Preset.** The extractor is its own preset, `extractor_v1`; generation and
  labelling stay in `recommended_v1`. Extraction settings default to
  Sonnet 5.5 at 0.0 and are compared with that preset for Recommended/Custom.
- **Migrations.** Analyses was never deployed, so its migrations are folded into
  `0001_initial` **(call)**. The taxonomy migration stays as it is.
- **Labels.** `label_key` also covers a digest of the system body the labeller
  reads, so a different body can never leave labels looking current **(call)**.
- **Body format** (chosen by the owner, 2026-11-25: the format of section 3).
  Elements as `- Id (Value)` under their sections in the prompt's order; each
  data flow on one line, `- Id (DataFlow): Source → Destination; data: X;
  boundary: Y`, from its approved facts; every other fact under "### Facts" as
  `- Subject field Value`; controls under "### 8. Controls" as
  `- Element: Control present|absent`. Then "Not covered by the description",
  then "## Original description" and the analyst's text. Rejecting a value
  drops only that value.
- **Run failure versus marked value.** The run fails if the reply is not a JSON
  object, or `elements`, `facts`, `controls` are not lists, or `gaps` is not an
  object. Everything else becomes a value with a problem: an unknown field, a
  value outside the allowed list or of the wrong kind (`retention_days` not a
  whole number, `removable_media` not true/false), a missing or duplicate
  element id, an id that names no element. A missing `section` is taken from the
  field's definition **(call)**.
- **Edited.** Each value keeps the model's original. An edited value counts as
  approved in the body; the report counts approved, edited (subset of approved)
  and rejected.
- **Start new from this description** copies the description only, as decided
  for the base design; the copy extracts afresh.
- **SmartStop** replaces the Qasr Tamar example, at the owner's request. Its
  domain (public transport) differs from the labeller's worked examples
  (Camden noise app, Oldsmar water plant).
