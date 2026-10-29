# Tool 4: evidence_pack (Bank-ready AI evidence pack generator)

Turns a client's **AI use-case register** into the core deliverables of the Sprint and the Assessment: a scored workbook, a 6-10 page report, a use-case x area heatmap and a 2-page board summary. Everything is generated from the input; nothing is hand-edited.

```
register (YAML or CSV) + optional org controls
  -> loader (validates, reports every problem at once)
  -> risk tiering (config/tiering.yaml: transparent rules, every match recorded)
  -> 27 checks in 8 areas (config/checks.yaml + checks.py) -> 1-4 score per use case per area, with evidence notes
  -> gaps (severity = weight x shortfall x tier factor) -> themes ranked -> 90-day plan
  -> evidence_pack.xlsx | evidence_pack.docx | heatmap.png | board_summary.docx
```

## Run
```bash
cd evidence_pack
pip install -r requirements.txt
./run_demo.sh                      # both sample packs, then the tests
python3 -m evidence_pack samples/fintech_120.yaml --out output/fintech_120
python3 -m evidence_pack samples/nbfc_12.csv --org samples/nbfc_org.yaml --out output/nbfc_12
```
Two synthetic samples are included and already generated in `output/`: **Nimbus Pay (Demo)**, a 120-person fintech with 8 use cases (YAML), and **Hemavati Finance (Demo)**, a mid-size NBFC with 12 use cases (CSV plus an organisation file). All names are fictional and marked "(Demo)".

## Input
One row or entry per use case. Required columns (headers are matched loosely, so "Business owner", "business_owner" and "Model/vendor" all work):

`id, name, business owner, purpose, model/vendor, data categories, personal data (yes/no), sensitive data (yes/no), users affected (customers / staff), decision impact (advisory / automated), human-in-the-loop (yes/no), hosting/data location, monitoring in place (none / basic / continuous), last validation date, incidents in last 12 months`

Optional columns: `client_risk_tier` and **control answers** (`yes`, `partial`, `no`, blank = not evidenced), because a register alone cannot show things like retention or exit plans: `dpdp_basis_documented, retention_defined, cross_border_assessed, evals_release_gate, bias_testing, security_testing, least_privilege, output_controls, explainability, customer_recourse, model_change_notice, vendor_exit_plan, vendor_no_training, incident_postmortem, fallback_plan`.

Organisation-level answers (also the default for every use case): `board_approved_ai_policy, governance_committee, inventory_review, risk_based_audit`. In YAML they sit under `org_controls`; with a CSV register pass an `--org org.yaml` file. Lists use `;` in CSV.

## The 8 areas and their references (`config/areas.yaml`)
| # | Area | Reference (as supplied) |
|---|---|---|
| 1 | Accountability | RBI FREE-AI rec. 14 |
| 2 | AI inventory | RBI FREE-AI rec. 23 |
| 3 | Data governance | RBI FREE-AI rec. 15; DPDP Act 2023 |
| 4 | Lifecycle and evals | RBI FREE-AI rec. 16 |
| 5 | AI security | OWASP GenAI Top 10; RBI FREE-AI recs. 19-20 |
| 6 | Human oversight | RBI FREE-AI principles (Sutras) |
| 7 | Vendor AI risk | OWASP supply-chain risks |
| 8 | Monitoring and audit | RBI FREE-AI recs. 21-24 |

**Every reference is printed with "to verify against the RBI FREE-AI report text before client use"** in the workbook, the report and the board summary. They have not been checked against the report text. The RBI draft Model Risk Management guidance (Jun 2026) is **not mapped yet**: add paragraph-level references under `additional_references` in `config/areas.yaml`.

## How scoring works
- Each check returns pass, partial, fail, not evidenced or not applicable. Points = weight x (1, 0.5, 0, 0). A score is the share of applicable points, banded 1 (<25%), 2 (25-55%), 3 (55-85%), 4 (85%+). Bands, weights, thresholds and wording are all in `config/`.
- Blank answers count as **not evidenced** (zero), not as "no", and the gap says "no evidence provided".
- Tier changes the standard: validation must be newer than 6 / 12 / 18 months for High / Medium / Low, and an automated decision without a human in the loop fails in the High tier, is partial in Medium.
- Tiering rules (`config/tiering.yaml`), highest match wins: T1 automated decisions about customers using personal data = High; T2 sensitive data affecting customers = High; T3 two or more incidents plus personal data = High; T4 any automated decision = Medium; T5 customer-facing with personal data = Medium; T6 personal data hosted outside India = Medium; T7 sensitive data = Medium; otherwise Low.
- Area scores across use cases are tier-weighted means (High x3, Medium x2, Low x1); the overall score is the mean of the eight.
- Gaps repeated across use cases are grouped into themes for the top-10 list and the plan; the full per-use-case list is in the Gaps sheet (due dates left blank).

## Outputs
| File | Contents |
|---|---|
| `evidence_pack.xlsx` | Inventory (with tier and why), Area scores (matrix, area summary, evidence notes per check), Gaps, 90-day plan, Assumptions |
| `evidence_pack.docx` | 7 pages for both samples: executive summary, scope, method, inventory and heatmap, findings by area, top 10 gaps, 90-day plan, disclaimer |
| `heatmap.png` | Use cases x 8 areas, score 1-4 in each cell (also readable without colour) |
| `board_summary.docx` | 2 pages: headline, area scores, top 5 gaps, decisions requested, plan at a glance, disclaimer |

## Limitations
- **Self-reported input.** Nothing is tested or verified. A client who ticks "yes" gets credit for it; the report says so.
- Scores show relative readiness on a transparent scheme **designed by the consultant**. It is not an RBI-prescribed or audited methodology, and the weights and bands are judgement calls to review and tune with a real client.
- The register does not hold some evidence (for example retention, exit plans), so those come from the optional control answers or count as not evidenced.
- Word-processor page counts were checked by converting with LibreOffice (7 pages and 2 pages); Word may paginate slightly differently.
- Not a formal audit. Formal cyber audits are for CERT-In empanelled auditors.
