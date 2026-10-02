---
id: jurisdictions/TJUR-0057
title: "Canada (Jurisdiction Profile)"
parent: jurisdictions
status: published
tags: ["jurisdiction", "canada", "international", "regulatory", "deep-data-candidate"]
relations: [relates_to=recalls/TRCL-0013, relates_to=recalls/TRCL-0014, relates_to=recalls/TRCL-0015, relates_to=recalls/TRCL-0016, relates_to=datasets/TDTS-0028]
summary: "Jurisdiction profile for Canada: federally legal adult-use cannabis under the Cannabis Act (Health Canada), provincial retail models, licensed-producer open data, subnational modeling required."
---

# Canada (Jurisdiction Profile)

{{include includes/jurisdiction-legal-disclaimer.md}}

## Jurisdiction Identity

| Field | Value |
| --- | --- |
| Country | Canada |
| ISO country code | CA |
| National authority | Health Canada (federal regulator and licensor) |
| Official sources | https://www.canada.ca/en/health-canada/services/drugs-medication/cannabis.html ; Cannabis Act: https://laws-lois.justice.gc.ca/eng/acts/c-24.5/ |
| Last verified | 2026-08-09 |

## Cannabis Framework

| Dimension | Status |
| --- | --- |
| Adult-use possession | Permitted federally (adults 18–19+ depending on province; up to 30 g in public) |
| Adult-use cultivation | Permitted (up to 4 plants per household) |
| Adult-use commercial supply | Legal and regulated federally; provincial/territorial retail models |
| Medical access | Federally regulated access to cannabis for medical purposes (ACMPR) |
| Pharmaceutical cannabinoid access | Regulated drug products (e.g., Sativex, Nabilone) via Health Canada |
| Home cultivation | Permitted (4 plants per household) |
| Import/export | Regulated and restricted; permits required under the Cannabis Act |

## Subnational Systems

Cannabis retail is administered by **provinces and territories** (government-operated stores in some provinces, licensed private retail in others). This page is a country-level profile; deeper subnational modeling will eventually be required for province-level retail and enforcement variance.

## Regulatory Overview

Canada legalized adult-use cannabis federally under the Cannabis Act (October 17, 2018), the first G7 country to do so. Health Canada licenses cultivators, processors, and sellers; the Cannabis Regulations impose good production practices, product standards, packaging/labeling, and testing requirements. A March 2025 omnibus regulatory amendment (SOR/2025-…) adjusted licensing and reporting obligations.

## Licensing Structure

Federal licences: cultivation (standard/micro/nursery), processing (standard/micro), sale for medical purposes, analytical testing, and research. The licensed-producer list is public and updated regularly.

## Laboratory / Quality Framework

- **Standards**: Cannabis Regulations require testing for potency, contaminants, microbials, pesticides, heavy metals, residual solvents, mycotoxins; production under Good Production Practices (GPP).
- **Analytical testing**: Licensed analytical testing facilities; no public per-batch COA database.
- **Quality reference**: Health Canada regulatory framework; Canadian cannabis quality standards align with GPP/GMP concepts but are cannabis-specific.

## Data Surface

| Data surface | Available? | Official source | Machine-readable? | Notes |
| --- | --- | --- | --- | --- |
| License registry | yes | Health Canada Licensed cultivators/processors/sellers table | partial | Public HTML table; updated regularly |
| Testing-laboratory registry | partial | Licensed analytical testing facilities | no | Part of licensing records |
| Laboratory testing rules | yes | Cannabis Regulations (SOR/2018-144) | no | Codified testing requirements |
| Contaminant/action limits | yes | Cannabis Regulations | no | Set in regulation |
| Recalls/advisories | yes | Health Canada cannabis recalls (Recalls and Safety Alerts database; open-data JSON/CSV feeds) | yes | Daily-updated machine-readable feed under the Open Government Licence – Canada |
| Product/package identifiers | yes | Licensed producers' product data; provincial listings | partial | Not a single national open registry |
| COAs/batch results | no public source located | — | — | No public federal batch database |
| Sales data | yes | Statistics Canada cannabis surveys and market data | yes | Official statistics |
| Open-data downloads | partial | canada.ca cannabis market data page | partial | Quarterly market stats (licensed area, production) |
| Traceability | no public source located | — | — | Licensed producers maintain records; no public track-and-trace feed |

## Recalls / Advisories

Health Canada publishes cannabis and consumer-product recalls through its
Recalls and Safety Alerts database (machine-searchable; daily-updated
JSON/CSV open-data feeds under the Open Government Licence – Canada).
Provincial bodies also publish public health notices. The archive models the
device-relevant Health Canada recall events that intersect the catalog:

- [Firewood 4 recall (2017)](../recalls/TRCL-0013.md)
- [HEXO disposable cannabis vape pens recall (2021)](../recalls/TRCL-0014.md)
- [Arizer Solo II recall (2025)](../recalls/TRCL-0015.md)
- [Arizer Solo III recall (2026)](../recalls/TRCL-0016.md)

## Future Ingestion Opportunities

1. Licensed cultivator/processor/seller list → organization/license entities (public table).
2. Recalls and Safety Alerts cannabis entries → recall/advisory entities (the device-relevant subset was swept 2026-09-06; the full cannabis feed remains a bulk-ingestion candidate).
3. Statistics Canada market data → aggregate dataset records.
4. Subnational (province) retail models need a `has_subjurisdiction` modeling decision before per-province records are created.

## Sources & Provenance

- **Statutory framework**: Cannabis Act, S.C. 2018, c. 16 (in force October 17, 2018); Cannabis Regulations (SOR/2018-144); current to 2026-06-17.
- **Regulator**: Health Canada — https://www.canada.ca/en/health-canada/services/drugs-medication/cannabis/laws-regulations.html
- **Market data**: https://www.canada.ca/en/health-canada/services/drugs-medication/cannabis/research-data/market.html
- **Recalls open data**: Health Canada, Recalls and Safety Alerts open-data feed (JSON/CSV, Open Government Licence – Canada) — https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json
- **Retrieval date**: 2026-08-09; recall feed retrieved 2026-09-06

## Graph Connections

Recall entities linked to this jurisdiction:
[Firewood 4 (TRCL-0013)](../recalls/TRCL-0013.md),
[HEXO disposable pens (TRCL-0014)](../recalls/TRCL-0014.md),
[Arizer Solo II (TRCL-0015)](../recalls/TRCL-0015.md), and
[Arizer Solo III (TRCL-0016)](../recalls/TRCL-0016.md).
The page is discoverable from the [Jurisdictions index](../jurisdictions.md).
