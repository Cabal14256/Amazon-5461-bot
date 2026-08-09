# Data Flow Reference

## When to read

Read this file when the request is about:
- where runtime materials come from
- Excel vs generated text files
- brand pack generation and consumption
- deciding which file is the source of truth during execution
- tracing how business data becomes automation input

## Scope

This reference covers the data path from human-maintained upstream sources into runtime files used by automation.
It should explain what gets edited by humans, what gets generated, and what execution should actually read.

## Primary goal

Keep runtime execution deterministic by separating upstream maintenance data from execution-ready materials.

## Data model overview

The project uses a staged data flow:
1. human-maintained source data
2. generated intermediate/runtime materials
3. automation execution reads the generated materials
4. evidence and ledgers record outcomes

This separation reduces ambiguity during live runs.

## Core source-of-truth rule

At runtime, automation should read execution-ready files, not jump back to upstream spreadsheets or ad hoc human notes.

Known project rule:
- Excel may be used for human maintenance and bulk management
- generated text/material files are the runtime source for automation

## Known pipeline

Current known pattern:
- Excel source such as `5461文案表.xlsx`
- generation step produces files under `brand_packs/{brand}/docs/`
- execution reads the generated files from `brand_packs/{brand}/docs/`

Short form:
- Excel → generated txt/docs → runtime execution

## Brand pack rule

`brand_packs/{brand}/docs/` should contain the materials actually consumed by flows.
This may include:
- localized text
- submission wording
- supporting notes
- file references for images or attachments

If a flow needs branded materials, prefer resolving them through the brand pack structure.

## Runtime discipline

During execution:
- do not switch back to Excel to fill live forms
- do not mix manual edits from multiple disconnected sources without reconciliation
- prefer deterministic reads from generated brand-pack files

If runtime files are missing or stale, stop and fix the generation/update step first.

## Localization rule

Localization should be reflected in the generated runtime materials.
Typical marketplace mapping:
- UK/US → English
- DE → German
- FR → French
- ES → Spanish
- IT → Italian

If localization is extended later, update the generation logic and the runtime expectations together.

## Asset naming rule

Known example convention:
- image naming format: `品牌-数字.jpg` such as `JZG-1.jpg`

Use this file to preserve additional naming or packaging conventions over time.

## Configuration relationship

Data flow should align with config resolution.
Typical pieces that interact:
- account config
- marketplace config
- brand pack manifest/content
- runtime scripts that load docs/assets

If there is disagreement between config and generated materials, resolve the inconsistency before live execution.

## Validation checklist

Before running a flow that depends on generated materials, verify:
- target brand pack exists
- expected docs/assets exist
- marketplace/language version exists
- generated files are current enough for the request
- config points to the same brand/material set intended by the operator

## Evidence expectations

When data-flow issues occur, capture:
- missing file paths
- stale or mismatched language/material set
- config values used to resolve the materials
- screenshots/logs showing runtime lookup failure

## Decision points

Typical future decisions to preserve here:
- what counts as stale generated data
- whether regeneration should be automatic or manual
- which files are required per flow type
- how localization variants are selected
- how manifests relate to docs and assets

## Related references

Read these when needed:
- `5461.md` for 5461-specific material usage
- `gtin.md` for GTIN-specific material and asset usage
- `account-mapping.md` for account/marketplace resolution
- `business-rules.md` for high-level data-source policy

## Open extensions

Good future additions for this file:
- explicit generation pipeline steps
- required file checklist by flow type
- manifest schema notes
- stale-data detection rules
- localization folder/file conventions
