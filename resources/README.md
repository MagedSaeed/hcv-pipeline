# Resources

Static research inputs. Nothing here is written by the application — the app
seeds itself from these files, then owns its own copies in the database.

```
resources/
├── docs/pipeline.png                     The 4-phase pipeline diagram
├── taxonomy/hcv21_v1.1.json              The 21 human-centric vulnerabilities (HCV-21 v1.1)
├── ontology/vocabulary_2026-08-26.{docx,md}
│                                         Classes, object properties, fixed values
└── seeds/oldsmar/
    ├── system_description.{docx,md}      Expert-authored system description (Phase 1)
    ├── scenarios.{docx,md}               Expert-authored scenario cards + narratives (Phase 1)
    └── abox.json                         Ontology encoding of the whole pack
```

`.md` files are generated from their `.docx` siblings by
`scripts/extract_docx.py` and are the source of truth for seeding — the app
ingests text, not Word XML. Word tables become GFM tables and heading styles
become ATX headings, so structure survives into the prompts. Regenerate after
editing a `.docx`:

    python3 scripts/extract_docx.py resources/seeds/oldsmar/scenarios.docx

Pass `--format html` to recover the source colour coding as semantic spans;
see below.

## What each file feeds

| File | Used by |
|------|---------|
| `taxonomy/hcv21_v1.1.json` | Seeds the `Taxonomy`/`HCV` tables. Given to Phase 2 Run B and always to Phase 3. Withheld from Phase 2 Run A by design. |
| `seeds/oldsmar/*.md` | Seeds one `SystemDescription` and its `Scenario` rows; also the basis of the Phase 2 few-shot pair. |
| `ontology/vocabulary_*.md` | Reference for the offline ontology-validation step. Optionally injectable into prompts as a resource. |
| `seeds/oldsmar/abox.json` | Reference only. ABox encoding is out of scope for phases 1-3. |

## Colour coding, and why Markdown drops it

The two seed `.docx` files mark vocabulary term *kind* by colour: blue bold a
class, green italic an object property, orange bold a fixed value, purple italic
a term with no vocabulary entry, bold-without-colour an individual. That is
semantic, not decorative — the legend at the top of the system description says
so. Markdown has no colour, so extraction drops it.

That is deliberate. The information is recoverable almost everywhere else:

| Kind | Recoverable from the vocabulary by name lookup |
|------|--------|
| fixed value | 100% |
| object property | 96-100% |
| class | 64-76%, the shortfall being classes the vocabulary omits |
| individual | 0%, but the prose already writes `EP1 (ExternalParty)` — 29 such pairs in the seed scenarios |
| undefined | 0%, and genuinely unique (see below) |

Nothing in phases 1-3 reads term kinds, and the seed scenarios are used as
Phase 2 few-shots where the HTML markup roughly doubles the token cost. When
ABox encoding needs the annotation, recover it with:

    python3 scripts/extract_docx.py --format html resources/seeds/oldsmar/scenarios.docx

which emits `<span class="term-class">…</span>` and friends. The `.docx` files
are kept in the repository precisely so that stays possible.

Purple italic is the one signal with no other source: it means no vocabulary
term exists for that phrase and the ontology needs extending. The Oldsmar seed
carries three — `process data`, `identity data`, `help with their bills`.

## Vocabulary gaps

Two inconsistencies in `vocabulary_2026-08-26`, recorded here because the
ontology-validation step will hit them. Neither is fixed by this repository.

- §1.1 declares `SystemElement (55)` but lists 33 rows. The gap corresponds to
  the ~21 `VulnerabilityCondition` subclasses that the HCV taxonomy references
  in `condition_classes` and that the vocabulary discusses in prose but never
  tabulates. `DataCategory` likewise declares 5 and lists 6.
- Ten classes used in the Oldsmar seed have no vocabulary entry at all:
  `OperatorWorkstation`, `SensingDevice`, `ActuatingDevice`, `AIService`,
  `SelfServicePortal`, `NetworkInfrastructure`, `GovernmentOrganization`,
  `CloudServiceProvider`, `IoTVendor`, `ManagedServiceProvider`.

The vocabulary's own rule is that everything named in a system description must
be an instance of a section 1 class, so these need reconciling upstream.

## Taxonomy note

4 of the 21 rows (HCV-09, HCV-14, HCV-19, HCV-20) carry an empty
`condition_classes` list. That is deliberate: the ontology has no condition
class for them, so a reasoner can never infer them. They stay in the label
vocabulary so that ontology coverage of the taxonomy is itself reportable.
