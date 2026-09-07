# Resources

Static research inputs. Nothing here is written by the application — the app
seeds itself from these files, then owns its own copies in the database.

```
resources/
├── docs/pipeline.png                     The 4-phase pipeline diagram
├── taxonomy/hcv21_v1.1.json              The 21 human-centric vulnerabilities (HCV-21 v1.1)
├── ontology/vocabulary_2026-08-26.{docx,txt}
│                                         Classes, object properties, fixed values
└── seeds/oldsmar/
    ├── system_description.{docx,txt}     Expert-authored system description (Phase 1)
    ├── scenarios.{docx,txt}              Expert-authored scenario cards + narratives (Phase 1)
    └── abox.json                         Ontology encoding of the whole pack
```

`.txt` files are generated from their `.docx` siblings by
`scripts/extract_docx.py` and are the source of truth for seeding — the app
ingests text, not Word XML. Regenerate after editing a `.docx`:

    python3 scripts/extract_docx.py resources/seeds/oldsmar/scenarios.docx

## What each file feeds

| File | Used by |
|------|---------|
| `taxonomy/hcv21_v1.1.json` | Seeds the `Taxonomy`/`HCV` tables. Given to Phase 2 Run B and always to Phase 3. Withheld from Phase 2 Run A by design. |
| `seeds/oldsmar/*.txt` | Seeds one `SystemDescription` and its `Scenario` rows; also the basis of the Phase 2 few-shot pair. |
| `ontology/vocabulary_*.txt` | Reference for the offline ontology-validation step. Optionally injectable into prompts as a resource. |
| `seeds/oldsmar/abox.json` | Reference only. ABox encoding is out of scope for phases 1-3. |

## Taxonomy note

4 of the 21 rows (HCV-09, HCV-14, HCV-19, HCV-20) carry an empty
`condition_classes` list. That is deliberate: the ontology has no condition
class for them, so a reasoner can never infer them. They stay in the label
vocabulary so that ontology coverage of the taxonomy is itself reportable.
