# Analyses: design

**Date:** 2026-11-22
**Status:** approved 2026-11-22
**Extends:** `2026-09-07-hcv-pipeline-design.md` (phases 1-3)

Items marked **(call)** were chosen by the designer and are open to correction.
Everything else was decided with the project owner or follows from the existing code.

## 1. Intent

The research determined a recommended setup for generating and labelling scenarios.
Analyses puts that setup in the hands of system analysts: they describe their system,
a generator writes scenarios for it, a labeller finds human-centric vulnerabilities
(HCVs) in those scenarios, and a report shows the threats each HCV enables.

Users are system analysts who know their system and need no knowledge of experiments,
variants, phases or prompt versions.

**Goals**

- An elegant, friendly flow in visible steps.
- The research's recommended setup by default, with settings the analyst can change.
- Work is saved: reopen, continue, rename, start new from, delete.
- Threats come from a fixed, versioned table in the taxonomy.
- By default the prompts are the research's, with its count of 8 scenarios.

**Non-goals**

- Changes to accounts, the API key or sharing.
- Editing prompts or worked examples inside Analyses.
- Comparing versions of an analysis.
- Ontology reasoning, severity scores or mitigations.

**This fails if**

- analysts' work appears in the corpus, labels, experiments or their exports;
- the interface says "few-shot, 5 examples" and the request carries none;
- a report mixes labels with a description or settings that did not produce them;
- the report reads as proof that a system has, or lacks, a vulnerability.

## 2. Approaches

| | Approach | Trade-off |
|---|---|---|
| A | Build on experiment tables | Reuses execution; mixes analyst work into research data and changes guarded research code |
| **B** | **New area in the same app, with its own tables** | **Chosen. Isolated and simple to lock and delete; needs a small runner** |
| C | Separate app | Cleanest product; second login and deployment, and copies that drift |

B reuses login, key resolution, model picker, taxonomy, `prompts.rendering`, response
schemas, the LLM client and django-q. It creates no `Experiment`, `PipelineRun`,
`PhaseRun`, `SystemDescription`, `Scenario`, `LLMCall` or `Label` rows.

## 3. Experience

### 3.1 Your analyses

```
 Your analyses                                        [+ New analysis]

 Smart Parking        ✓ System  ✓ Generate  ✓ Label  ✓ Report    ⋯
 Clinic Booking App   ✓ System  ✓ Generate  ○ Label  ○ Report    ⋯
                     ⋯ Rename · Start new from this description · Delete
```

- "Analyses" is the first navigation item and the page after sign-in **(call)**.
- The list shows the user's own analyses, with an "Everyone's" switch **(call)**.
- A row reopens the step where work stopped.
- "Start new from this description" creates an editable analysis named "Name (2)" with
  the description only. Settings are Recommended; nothing else is copied.
- Delete asks once, then removes the description, scenarios, labels, runs and archived
  calls. It is allowed during a run; the worker then stops quietly **(call)**.

### 3.2 The four steps

```
 (1) System ───── (2) Generate ───── (3) Label ───── (4) Report
```

Each step waits for the analyst. Finished steps can be revisited. Runs continue when
the tab is closed.

**1. System**

- A name and one large text box, saved automatically. Limit 50,000 characters **(call)**.
- "Insert an outline" adds six headings: Purpose and scope; Elements; People and
  organisations; Data flows; Settings and procedures in force; What is not in place.
- "Try an example" fills in a fictional system that is neither Oldsmar nor NoiseApp.
  Its text is drafted during the build and reviewed by the owner **(call)**.
- The description locks when Generate starts. It unlocks if generation fails or is
  cancelled and no generated scenarios exist. Once locked, the step shows why, with
  "Start new from this description".
- Rename changes the name shown in the list and report, at any time. The prompts include
  the system's name, so they keep the one it had when the description locked **(call)**.

**2. Generate**

```
 Generate scenarios
 Recommended: Claude Sonnet 5 · 0.8 · zero-shot                  [change]

 Number of scenarios  [ 8 ]

 Scenarios · 9 included, 1 set aside                               ⋯
                                    Add your own scenario · Show set aside
 1  An operator approves a reroute without checking                ⋯
                                                          Set aside
 9  A cleaner finds a badge in a meeting room   [Added manually]   ⋯
                                           Edit · Set aside · Delete

                              [Generate again]     [Label scenarios →]
```

- The button reads "Generate scenarios (the description will be locked)".
- If the selected prompt takes a number, the step shows "Number of scenarios" directly
  above the Generate button: 8 by default, as in the research, adjustable from 1 to 20.
  For a prompt that takes no number the field is hidden and the model decides.
- If the model returns a different number, keep them and say so.
- No scenarios is a failure. A duplicate id is renumbered.
- Generated scenarios are read-only and all included. They can be set aside and restored.
- Own scenarios can be added after the first generation **(call)**. Only "What
  happens?" is required; the card fields are optional.
- Own scenarios carry a coloured "Added manually" tag on Generate, Label, both report
  tabs and the PDF. The tag is not sent to the labeller, and their ids are neutral
  (`sc_101`, `sc_102`, ...) so that it judges them like the others **(call)**.
- At most 30 scenarios are included in an analysis **(call)**. Generation is refused if
  a set number plus the included own scenarios exceeds 30, or if those alone reach 30.
  If the model returns more than fit, the extra ones are saved as set aside, with a notice.
- A card's title is the first line of its trigger, or of "What happens?" **(call)**.
- "Generate again" uses the same description. After confirmation, and only once the
  new run succeeds, it replaces the generated scenarios with their labels and set-aside
  state. Own scenarios and their labels stay.

**3. Label**

- Labels appear scenario by scenario, with evidence and rationale. Evidence is titled
  "From the scenario", because it does not quote the analyst's description **(call)**.
- A scenario with no labels reads "No vulnerability found in this scenario".
- The main button labels only included scenarios without current labels.
- Editing an own scenario, or changing any labeller setting, marks the affected labels
  out of date. Relabelling replaces them one scenario at a time, on success.
- Failed scenarios show the reason and "Retry failed".

**4. Report**

```
 Smart Parking: 6 potential vulnerabilities
 [ Vulnerabilities ]  [ Scenarios ]                              Download

 Misinterpreted Emotional Responses      found in 2 scenarios · 1 added manually
   Enables   Unawareness · Data disclosure · Non-compliance
   At risk   The EmotionRecognitionService itself
   Why       rationale, with the evidence from the scenario
   See how it could happen →
 ▸ Research details
```

- Each finding counts its manual scenarios. One found only in those reads "Only in
  manually added scenarios" **(call)**.
- Grouped by HCV, ordered by number of scenarios, then HCV id **(call)**.
- STRIDE and LINDDUN threats are visually distinguished.
- Uses only current labels of included scenarios. If some are unlabelled, failed or
  out of date, a banner gives the count. Setting aside and restoring update it
  immediately, and it lists what was set aside.
- Always carries: "These are potential vulnerabilities found in generated or
  analyst-written scenarios. Check them against the real system. A vulnerability that
  is not listed may still be present."
- The generator's own `threat` field appears only inside scenario details, as
  "Threat suggested by the generator". Report threats come only from the table **(call)**.
- "Research details" holds HCV ids, condition classes, rules, taxonomy version, models,
  temperatures, prompts, examples, scenario count setting, and whether each setup was Recommended or Custom.
- Download is a print layout saved as PDF through the browser **(call)**.

### 3.3 Settings

Each model step has a "change" side panel with "Reset to recommended". Any change
shows "Custom". Settings cannot change while a run is active.

| Setting | Recommended, the default | Can change to |
|---|---|---|
| Generation model | `anthropic/claude-sonnet-5` | any model in the picker |
| Generation temperature | 0.8 | 0 to 2 |
| Generation prompt | "Zero-shot Variant B" v1 | any active variant B generation prompt |
| Generation examples | none (zero-shot) | any active generation examples |
| Number of scenarios | 8 | 1 to 20, if the prompt takes a number |
| Labelling model | `openai/gpt-5.6-luna-pro` | any model in the picker |
| Labelling temperature | 0.0 | 0 to 2 |
| Labelling prompt | "default-labelling" v1 | any active labelling prompt |
| Labelling examples | the research's five (few-shot) | any active labelling examples, or none |
| Taxonomy | HCV-21 v1.2 | another version, until the first generation starts **(call)** |

```
 Prompt
 (•) Zero-shot Variant B          [Recommended]
 ( ) default-generation-variant-b
 ( ) Diverse-generation-variant-b   writes control scenarios, not marked

 Worked examples
 (•) None, zero-shot              [Recommended]
 ( ) Choose from the list           [ ] NoiseApp   [ ] oldsmar
```

- The lists are the app's existing prompts and examples. Analysts select; editing stays
  on the research pages.
- The Recommended entry is the pinned preset (section 5). It is first, tagged and
  selected, and replaces a list row with identical content. It stays the same if the
  research rows are later edited **(call)**.
- Only variant B generation prompts are listed (the generator sees the taxonomy). A
  prompt is B if its stored variant is B or, where an older row stores none, if it
  inserts the taxonomy.
- "Zero-shot" means no examples selected. It is offered in both steps.
- Other selections are copied by content when a run starts, including the resources
  they use, so later edits or deletions do not change past results. A selection deleted
  before the run must be chosen again.
- A prompt containing `is_control` is noted "writes control scenarios, not marked",
  because the response schema drops that flag **(call)**.
- A prompt that cannot render for a supplied system shows the reason before any
  provider call is made.

Temperature is a normal setting in both steps, as in the experiment setup: always
editable, defaulting to 0.8 and 0.0, saved, sent with every call and shown in the report.
For a model that does not list temperature in OpenRouter's `supported_parameters`, a hint
under the field reads "This model may ignore temperature" **(call)**. On 2026-11-22 this
applies to both recommended models.

An example citing an HCV id that the chosen taxonomy lacks cannot be selected, and the
panel says why.

## 4. Taxonomy and threats

- `HCV` gains `threat_mappings`, a JSON list of
  `{condition_class, element, rule, stride[], linddun[], qualifier}`.
- The importer, serializer, "new version" copy, Taxonomy page and taxonomy export all
  carry the field. Categories are validated against the six STRIDE and seven LINDDUN values.
- `resources/taxonomy/hcv21_v1.2.json` copies ids, names and definitions from v1.1 and
  takes condition classes and mappings from the table below. v1.1 is not edited.
- Deployment imports v1.2 with `--if-missing` and without `--activate`. Research keeps
  its active taxonomy. Before the first deploy, check production for an existing v1.2
  (the September dump has only v1.1). If the name is taken, ship this file under the
  next free version and use that number throughout **(call)**.
- A separate "default for analyses" flag selects the taxonomy of new analyses. v1.2
  gets it at import only if no version has it. Staff can move it on the Taxonomy page **(call)**.
- A taxonomy used by an analysis that has started generation is frozen, by extending
  `taxonomy_lock_reason`. A changed mapping is a new version.

The mapping is authoritative as given by the owner:

| HCV | Condition class | Element at threat | Rule | STRIDE | LINDDUN |
|---|---|---|---|---|---|
| 01 | LimitedAuthenticationInvolvementCondition | Element protected by the AuthenticationMechanism | E1 | Spoofing | - |
| 02 | PoorParticipatoryInterfaceCondition | Service behind the Interface | E2 | - | Unawareness |
| 03 | UnmonitoredAutomationCondition | The Service itself | - | Tampering; Denial of service | - |
| 04 | MissingManualOverrideCondition | The Service itself | - | Denial of service | - |
| 05 | ConsequentialEmotionDecisionCondition | The EmotionRecognitionService itself | - | - | Unawareness; Data disclosure; Non-compliance |
| 06 | InboundExternalCommunicationCondition | The inbound DataFlow itself | - | Spoofing; Information disclosure | - |
| 07 | PrivilegedInsiderAccessCondition | Asset reached through the AccessGrant | E7 | Tampering; Information disclosure; Denial of service; Elevation of privilege | - |
| 08 | RemovableMediaEndpointCondition | The SharedEndpoint or OperatorWorkstation itself | - | Tampering; Information disclosure; Elevation of privilege | - |
| 10 | UnchallengedPhysicalEntryCondition | RestrictedArea behind the PhysicalEntryPoint | E10 | Spoofing; Tampering; Information disclosure; Elevation of privilege | - |
| 11 | UnverifiedDomainAccessCondition | Service behind the WebAccessChannel | E11 | Spoofing; Information disclosure | - |
| 12 | UnverifiedContactChannelCondition | The outbound DataFlow itself | - | Spoofing; Information disclosure | - |
| 13 | PermissiveDefaultPrivacyCondition | The Service itself | - | - | Data disclosure; Unawareness; Non-compliance |
| 13 | UnscopedDevicePermissionCondition | Service delivered by the MobileApp | E13c | Information disclosure | Data disclosure; Unawareness; Non-compliance |
| 15 | UninformedCollectionCondition | The UserInputFlow itself | - | - | Data disclosure; Unawareness; Non-compliance |
| 15 | UninformedTransferCondition | The DataFlow to the third party provider itself | - | - | Data disclosure; Unawareness; Non-compliance |
| 16 | UnmanagedDisposalCondition | The DataStore itself | - | Information disclosure | Data disclosure; Non-compliance |
| 17 | UnprotectedPasswordAuthenticationCondition | Element protected by the AuthenticationMechanism | E17 | Spoofing | - |
| 18 | UnreviewedAccessGrantCondition | Asset reached through the AccessGrant | E18 | Elevation of privilege; Information disclosure | - |
| 21 | ShippedDefaultConfigurationCondition | The IoTDevice itself | - | Spoofing; Tampering; Information disclosure; Elevation of privilege | - |

Display rules:

- The mapping is a lookup on HCV id. No model is involved.
- With several rows, threats common to all are shown plainly. A threat in only some
  rows carries that row's `qualifier`. The only such case is HCV-13's Information
  disclosure: "if through mobile app permissions".
- All elements at threat of the matched HCV are listed.
- HCV-09, 14, 19 and 20 have no row. They still appear, with "No threat mapping defined
  for this vulnerability".

## 5. Recommended preset

The preset is versioned files in `resources/presets/`, extracted once from the exports
in `post_genration_work/similar_scenarios_analysis/exports/` (gitignored, on the
owner's machine). The Recommended entries do not read editable prompt, example or resource rows.

| Part | Source |
|---|---|
| Generation | "Zero-shot Variant B" v1, from any Zero-shot export |
| Its vocabulary, `hcv-vocabulary-basics` | the rendered prompt, between the line ending "fixed value." and "## How to write a scenario", without the surrounding blank lines |
| Labelling | "default-labelling" v1 |
| Five labelling examples | the active phase-3 examples, identical in all five exports, in their exported order |

Templates call `resource(name)`, which reads the database. The analysis renderer must
resolve those names from the run's pinned content, through a small optional resolver argument or an
equivalent that leaves research rendering unchanged **(call)**.

Verified on 2026-11-22 against the exports:

- 160 of 160 labelling prompts re-render identically, also with changed condition classes;
- 8 of 8 zero-shot generation prompts re-render identically with the extracted vocabulary;
- the zero-shot B prompt renders in supplied-system mode (`generate_systems=False`,
  `target_system` set).

Both recommended model ids exist on OpenRouter (2026-11-22). Not yet verified: how they
perform here. The exports and the September database dump contain no run with either
model and none on a supplied system.

## 6. Backend

A new Django app, `analyses`. Names are the builder's; the responsibilities are:

- **Analysis:** owner, display name, prompt title, description, taxonomy, settings for
  each step, preset version, timestamps.
- **Analysis scenario:** card fields, narrative, generated or own, set aside, order.
- **Analysis run:** generate or label, status, pinned settings, counts, error.
- **Analysis call:** exact request and response, tokens, cost, latency, error.
- **Analysis label:** scenario, HCV, evidence, rationale, call, and enough to tell
  whether it is current for the scenario's content and the labeller's settings.

Rules:

- Everything cascades from the analysis. The taxonomy and HCV references are protected.
- The server enforces locks, read-only scenarios and limits.
- At most one queued or running run per analysis.
- Generation uses supplied-system mode, with `n_systems=1` and the matching schema.
- Labelling makes one call per scenario, in order, saving after each. It discards
  unknown HCV ids from the report and keeps them on the call.
- Cancel is honoured between calls. A response that arrives after cancel or delete is
  not saved as scenarios or labels.
- Each scenario is labelled in its own queued task, which queues the next one
  **(call)**. One call can take over 360 s (120 s timeout, two SDK retries), so 30
  would not fit in one 3600 s task.
- A running run with no progress for longer than the worker timeout is shown as failed
  and can be retried. A task that finishes after that saves nothing.
- Provider errors use the existing plain-language descriptions. A missing key or
  unavailable queue is reported before anything is locked.
- Permissions follow the current setup: signed-in users read, staff write and run,
  including on others' analyses. Analysts are therefore created with `--staff`, which
  also lets them change the API key, prompts, taxonomies and experiments. Non-staff
  users see "Everyone's" analyses read-only, without action buttons.

## 7. Frontend

React, Tailwind and react-query as they exist, with no new UI dependency. Routes are
`/analyses` and `/analyses/:id/:step`. Visual style is quiet and document-like: one
column, generous spacing, one primary action per step, plain words. Secondary actions
live in three-dot menus. Menus and panels work by keyboard; the steps work on a phone.
Progress is polled.

## 8. Tests and acceptance

- **Prompt parity:** committed fixtures from the exports reproduce the research prompts
  byte for byte, for zero-shot generation and for labelling.
- **Selection:** the default is the preset; a listed prompt and chosen examples are what
  gets sent; the count is 8 unless changed; a prompt
  taking no number shows no number field.
- **Manual tag:** shown in every place listed above and absent from the request.
- **Examples reach the model:** a default labelling request contains exactly five; a
  zero-shot one contains none.
- **Isolation:** a full analysis creates no research rows and appears in no research
  list or export. The existing suites still pass.
- **Mapping:** all 19 rows, the HCV-13 qualifier and the four unmapped HCVs.
- **States:** lock and unlock, regenerate, set aside and restore, own-scenario edit,
  stale labels, partial failure, retry, cancel, delete during a run, start new from.
- **Deletion:** no analysis text remains in any table after delete.
- **User text:** a description citing HCV ids and names generates normally.
- **End to end:** the whole flow in a browser with the fake client.
- **Before analysts use it:** with the owner's consent to the cost, a live run of both
  recommended models on 3 to 5 real descriptions, reviewed by the owner.

## 9. Left to the builder

Model and endpoint names, the staleness representation, file layout inside the app and
preset folder, component breakdown, polling interval, and microcopy not quoted here.
