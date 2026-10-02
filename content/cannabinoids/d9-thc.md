---
id: cannabinoids/TCBN-0009
title: "Δ9-Tetrahydrocannabinol (Δ9-THC)"
parent: cannabinoids
status: published
tags: ["cannabinoid", "phytocannabinoid", "neutral"]
relations: [relates_to=cannabinoids/TCBN-0007, relates_to=cannabinoids/TCBN-0008, relates_to=cannabinoids/TCBN-0002, relates_to=reference/TREF-0003]
summary: Principal psychoactive phytocannabinoid and decarboxylation product of THCA; the only neutral cannabinoid with measured vapor-pressure data in this archive.
---

# Δ9-Tetrahydrocannabinol (Δ9-THC)

## Identity

| Property | Value |
| --- | --- |
| Preferred name | Δ9-Tetrahydrocannabinol (Δ9-THC) |
| IUPAC name | (6aR,10aR)-6,6,9-trimethyl-3-pentyl-6a,7,8,10a-tetrahydro-6H-benzo[c]chromen-1-ol |
| CAS number | 1972-08-3 |
| PubChem CID | 16078 |
| InChIKey | CYQFCXCEBYINGO-IAGOWGBFSA-N [^1] |
| Molecular formula | C21H30O2 |
| Molecular mass | 314.46 g/mol (exact 314.2246 Da) |
| Compound class | Phytocannabinoid; neutral (non-acidic) cannabinoid |
| Stereochemistry | Natural material is (−)-trans-(6aR,10aR). Δ8-THC (CAS 5957-75-5) is a distinct positional isomer; never collapse the two in reporting [^1] |
| Major synonyms | THC, Δ9-THC, dronabinol (INN); decarboxylation product of THCA |

### Identity notes

- Δ9-THC and Δ8-THC are distinct positional isomers with distinct CAS numbers (1972-08-3 vs. 5957-75-5) and must not be collapsed [^1].
- The neutral compound is the decarboxylation product of THCA; GC without derivatization converts THCA to THC in the injector, so "Total THC" figures mix measured and converted material [^2].
- THCV (C19, propyl side chain) and THC (C21, pentyl side chain) are distinct homologues [^1].

## Physical properties

| Property | Value | Conditions / Notes |
| --- | --- | --- |
| Boiling point | Predicted normal boiling temperature ≈417 °C (690.4 K) at 1 atm; extrapolated from vapor-pressure data, not an observed phase change | Direct vapor-pressure measurements covered ≈25–121 °C; decomposition prevents a direct atmospheric boiling observation [^3][^10] |
| Vapor pressure | Measured ≈2.6×10⁻⁵ Pa at 25 °C to ≈0.22 Pa at 121 °C | Lovestead & Bruno 2017, direct measurement [^3] |
| Melting point | No sharp melting point; viscous oil/semi-solid at room temperature | |
| logP (octanol-water) | ≈5.7 (reported) | Highly lipophilic |
| Water solubility | Practically insoluble | Soluble in ethanol, methanol, chloroform, oils, CO₂ |
| Thermal decomposition | In the cited GC-injector study, Δ9-THC loss reached 17.2% at the study's 300 °C inlet condition and CBN increased; no universal decomposition threshold is assigned | Injector temperature and residence time are study conditions, not a device sample temperature [^4] |
| Oxidation / light sensitivity | Oxidation is condition-dependent; CBN formation is reported under prolonged air/heat exposure [^4] | Store opaque, inert |
| Known degradation products | Cannabinol (CBN, oxidation), Δ8-THC (isomerization), quinones (pyrolysis) | [^4][^5] |

> **Boiling point is not a device setpoint.** The commonly repeated ≈155–157 °C figure is associated with low-pressure vaporization or a device setting, not an atmospheric boiling observation. The predicted thermodynamic boiling point of pure Δ9-THC is ≈417 °C at 1 atm, while device chamber temperature is not sample temperature and material in plant matrix lags the setpoint [^3][^10].

## Thermal-extraction context

{{include includes/boiling-point-vs-device-note.md}}

THC is the neutral product of THCA decarboxylation; conversion is temperature-, time-, atmosphere-, and matrix-dependent, so no universal onset is assigned here [^2]. It is released from botanical matrix by vapor-pressure-driven evaporation. Because vapor pressures are measured only below ≈121 °C, the evaporation rate at common device operating temperatures is an extrapolation, not a measurement; device setpoint and sample temperature are distinct [^3]. The thermodynamic boiling point of the pure compound is not a device setpoint or a sample temperature.

## Cannabis occurrence

- The principal intoxicating constituent of drug-type cannabis; measured drug-type flower batches in legal-market COA datasets report Δ9-THC (and THCA) across a wide batch-to-batch range [^6].
- Values are batch- and report-attached; no universal cultivar claim is made. Consult [Lab Results](../lab-results.md) for batch-level measurements.

## Biosynthesis and processing

Δ9-THC is biosynthesized as THCA (by THCAS from the shared CBGA pool) and converted to the neutral compound by decarboxylation during drying, curing, and heating [^2][^7]. Prolonged oxidation converts THC to CBN [^4].

## Reported biological activity

### Human evidence
Δ9-THC is the principal psychoactive constituent of cannabis; its acute effects are mediated by the CB1 cannabinoid receptor, established in controlled human pharmacology [^8][^9]. This archive does not assess therapeutic efficacy or public-health policy.

### Preclinical animal and in vitro evidence

{{include includes/preclinical-evidence-note.md}}

CB1/CB2 receptor pharmacology is well characterized in vitro [^9]; behavioral and physiological effects in animals do not by themselves establish human outcomes.

### Industry claims
Device marketing that equates a single "boiling point" with a recommended temperature setpoint, or that implies boiling-point tables determine extraction efficiency, is not supported by the measured vapor-pressure data [^3][^10].

## Degradation products

- Primary: CBN (oxidation), Δ8-THC (isomerization), quinones (pyrolysis) [^4][^5]

## Sources

[^1]: PubChem CID 16078, Dronabinol (Δ9-Tetrahydrocannabinol, CAS 1972-08-3). Verified 2026-08-09.
[^2]: Dussy FE, Hamberg C, Luginbühl M, Schwerzmann T, Briellmann TA. Isolation of Δ9-THCA-A from hemp and analytical aspects concerning the determination of Δ9-THC in cannabis products. *Forensic Sci Int.* 2005;149(1):3–10. PMID 15734104. (GC-injector conversion of THCA-A to Δ9-THC.)
[^3]: Lovestead TM, Bruno TJ. Determination of cannabinoid vapor pressures to aid in vapor phase detection of intoxication. *Forensic Chem.* 2017;5:79–85. doi:10.1016/j.forc.2017.06.003. PMID 29266138. (Measured Δ9-THC and CBD vapor pressures; derived normal boiling point.)
[^4]: García-Valverde MT, Sánchez-Carnerero Callado C, Díaz-Liñán MC, et al. Effect of temperature in the degradation of cannabinoids: from a brief residence in the gas chromatography inlet port to a longer period in thermal treatments. *Front Chem.* 2022;10:1038729. doi:10.3389/fchem.2022.1038729.
[^5]: Turner CE, Elsohly MA, Boeren EG. Constituents of Cannabis sativa L. XVII. A review of the natural constituents. *J Nat Prod.* 1980;43(2):169–234. doi:10.1021/np50008a001. PMID 6991645. (Constituent inventory incl. THC degradation/isomerization chemistry.)
[^6]: Jikomes N, Zoorob M. The cannabinoid content of legal cannabis in Washington State varies systematically across testing facilities and popular consumer products. *Sci Rep.* 2018;8:4519. doi:10.1038/s41598-018-22755-2. (Legal-market COA dataset; batch-attached ranges.)
[^7]: de Meijer EPM, Bagatta M, Carboni A, Crucitti P, Moliterni VMC, Ranalli P, Mandolino G. The inheritance of chemical phenotype in Cannabis sativa L. *Genetics.* 2003;163(1):335–346. PMID 12586720. (Chemotype I/II/III inheritance.)
[^8]: Gaoni Y, Mechoulam R. Isolation, structure, and partial synthesis of an active constituent of hashish. *J Am Chem Soc.* 1964;86(8):1646–1647. doi:10.1021/ja01062a046. (Isolation and structure of Δ9-THC.)
[^9]: Pertwee RG. The diverse CB1 and CB2 receptor pharmacology of three plant cannabinoids: Δ9-tetrahydrocannabinol, cannabidiol and Δ9-tetrahydrocannabivarin. *Br J Pharmacol.* 2008;153(2):199–215. doi:10.1038/sj.bjp.0707442. PMID 17828291. (Receptor pharmacology review.)
[^10]: Eyal AM, Berneman Zeitouni D, Tal D, Schlesinger D, Davidson EM, Raz N. Vapor pressure, vaping, and corrections to misconceptions related to medical cannabis' active pharmaceutical ingredients' physical properties and compositions. *Cannabis Cannabinoid Res.* 2023;8(3):414–425. doi:10.1089/can.2021.0173. PMID 35442765. (Boiling-point figures in marketing are not thermodynamic boiling points; vapor-pressure data are scarce.)

## Related pages

- [Cannabinoids Index](../cannabinoids.md)
- [THCA Record](thca.md) (acid precursor)
- [THCV Record](thcv.md) (propyl homologue)
- [CBD Record](cbd.md) (sibling biosynthetic branch)
