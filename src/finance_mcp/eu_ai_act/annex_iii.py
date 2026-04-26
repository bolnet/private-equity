"""
annex_iii — Frozen Annex III high-risk AI system categories from
Regulation (EU) 2024/1689 (the "AI Act").

Source of record (public, verifiable):
  EUR-Lex, CELEX:32024R1689, Annex III.
  https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689

Annex III enumerates the eight broad areas in which an AI system is
classified as **high-risk** under Article 6(2). Each area in the regulation
contains one or more sub-points; the list below freezes the structure of
the published Annex III so the audit tool can run offline and air-gapped.

The wording of each category below is a faithful summary of the published
regulation text (not a verbatim copy) so any auditor can compare line for
line against EUR-Lex. Every entry carries the regulation citation that
permits independent verification.

Use case keys (snake_case) are stable identifiers used by the tool's
public API (`use_case_category` argument). Add new keys, never rename
existing ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


REGULATION_CITATION: str = (
    "Regulation (EU) 2024/1689 of the European Parliament and of the "
    "Council of 13 June 2024 laying down harmonised rules on artificial "
    "intelligence (Artificial Intelligence Act)."
)

REGULATION_URL: str = (
    "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689"
)


@dataclass(frozen=True)
class AnnexIIICategory:
    """A single Annex III high-risk category.

    Attributes:
        key:        Stable snake_case identifier (public API).
        annex_ref:  Annex III paragraph (e.g. "Annex III §5(b)").
        area:       One of the 8 thematic areas listed in Annex III.
        title:      Short human-readable label.
        summary:    Faithful summary of the regulation text — NOT verbatim;
                    auditors should compare against EUR-Lex.
        examples:   PE-relevant portco use cases in this category.
    """

    key: str
    annex_ref: str
    area: str
    title: str
    summary: str
    examples: tuple[str, ...]


# Annex III, paragraphs 1–8.
# Order and numbering preserved from the published regulation.
_CATEGORIES: tuple[AnnexIIICategory, ...] = (
    AnnexIIICategory(
        key="biometrics",
        annex_ref="Annex III §1",
        area="Biometrics",
        title="Remote biometric identification, categorisation, emotion inference",
        summary=(
            "AI systems intended to be used for remote biometric "
            "identification (excluding verification of identity), "
            "biometric categorisation according to sensitive attributes, "
            "or emotion recognition — insofar as their use is permitted "
            "under applicable Union or national law."
        ),
        examples=(
            "facial recognition at venues",
            "voice-based emotion analytics for call centres",
        ),
    ),
    AnnexIIICategory(
        key="critical_infrastructure",
        annex_ref="Annex III §2",
        area="Critical infrastructure",
        title="Safety components of critical infrastructure",
        summary=(
            "AI systems intended to be used as safety components in the "
            "management and operation of critical digital infrastructure, "
            "road traffic, or the supply of water, gas, heating and "
            "electricity."
        ),
        examples=(
            "grid-balancing optimiser at a utility portco",
            "SCADA anomaly detector at a water company",
        ),
    ),
    AnnexIIICategory(
        key="education",
        annex_ref="Annex III §3",
        area="Education and vocational training",
        title="Access, admission, evaluation, monitoring of learners",
        summary=(
            "AI systems intended to determine access to or admission to, "
            "or to evaluate learning outcomes within, educational and "
            "vocational training institutions; or to assess the "
            "appropriate level of education; or to monitor and detect "
            "prohibited behaviour during tests."
        ),
        examples=(
            "edtech portco's automated essay grader",
            "online proctoring system",
        ),
    ),
    AnnexIIICategory(
        key="employment",
        annex_ref="Annex III §4",
        area="Employment, workers management, access to self-employment",
        title="Recruitment, performance evaluation, work allocation",
        summary=(
            "AI systems intended to be used for the recruitment or "
            "selection of natural persons (in particular to place "
            "targeted job advertisements, screen or filter applications, "
            "and evaluate candidates); or to make decisions affecting "
            "terms of work-related relationships, the promotion or "
            "termination of relationships, the allocation of tasks based "
            "on individual behaviour or personal traits, or to monitor "
            "and evaluate the performance and behaviour of persons in "
            "such relationships."
        ),
        examples=(
            "HR-tech portco's CV ranking model",
            "warehouse productivity-tracking AI",
            "gig-platform task allocation engine",
        ),
    ),
    AnnexIIICategory(
        key="essential_services",
        annex_ref="Annex III §5",
        area="Access to essential private and public services and benefits",
        title="Eligibility for benefits, credit scoring, insurance, emergency triage",
        summary=(
            "AI systems intended to be used (a) by public authorities to "
            "evaluate eligibility for essential public assistance "
            "benefits and services or to grant, reduce, revoke, or "
            "reclaim such benefits; (b) to evaluate the creditworthiness "
            "of natural persons or to establish their credit score, with "
            "the exception of AI systems used for the purpose of "
            "detecting financial fraud; (c) for risk assessment and "
            "pricing in life and health insurance; (d) to evaluate and "
            "classify emergency calls or to dispatch emergency first "
            "response services."
        ),
        examples=(
            "consumer-lending portco credit-decisioning model",
            "BNPL underwriting AI",
            "health-insurance claims-pricing AI",
        ),
    ),
    AnnexIIICategory(
        key="credit_decisioning",
        annex_ref="Annex III §5(b)",
        area="Access to essential private and public services and benefits",
        title="Creditworthiness evaluation and credit scoring",
        summary=(
            "AI systems intended to be used to evaluate the "
            "creditworthiness of natural persons or to establish their "
            "credit score (with the exception of AI systems used for the "
            "sole purpose of detecting financial fraud). High-risk by "
            "express enumeration in Annex III §5(b)."
        ),
        examples=(
            "consumer-lending portco's underwriting model",
            "auto-finance credit-decision AI",
            "BNPL approval engine",
        ),
    ),
    AnnexIIICategory(
        key="insurance_pricing",
        annex_ref="Annex III §5(c)",
        area="Access to essential private and public services and benefits",
        title="Risk assessment and pricing in life and health insurance",
        summary=(
            "AI systems intended to be used for risk assessment and "
            "pricing in relation to natural persons in the case of life "
            "and health insurance."
        ),
        examples=(
            "life-insurance underwriting AI at an insurtech portco",
            "health-insurance pricing model",
        ),
    ),
    AnnexIIICategory(
        key="law_enforcement",
        annex_ref="Annex III §6",
        area="Law enforcement",
        title="Risk assessment, polygraph, evidence evaluation, profiling",
        summary=(
            "AI systems intended to be used by, or on behalf of, law "
            "enforcement authorities or by Union institutions, bodies, "
            "offices or agencies in support of law enforcement "
            "authorities — for assessing the risk of an individual "
            "offending or reoffending, as a polygraph or similar tool, "
            "for evaluating the reliability of evidence, or for profiling "
            "natural persons in the course of detection, investigation, "
            "or prosecution of criminal offences."
        ),
        examples=(
            "predictive policing analytics",
            "evidence-reliability scoring AI",
        ),
    ),
    AnnexIIICategory(
        key="migration_asylum",
        annex_ref="Annex III §7",
        area="Migration, asylum and border control management",
        title="Risk assessment, examination of applications, polygraph at borders",
        summary=(
            "AI systems intended to be used by competent public "
            "authorities (or on their behalf) for risk assessments in "
            "respect of natural persons in the context of migration, "
            "asylum, and border control; for examining applications for "
            "asylum, visa, residence permits, and associated complaints; "
            "or as polygraphs or similar tools."
        ),
        examples=(
            "border-control risk-scoring AI",
            "visa-application triage AI",
        ),
    ),
    AnnexIIICategory(
        key="justice_democracy",
        annex_ref="Annex III §8",
        area="Administration of justice and democratic processes",
        title="Judicial decision support, election influence",
        summary=(
            "AI systems intended to be used by a judicial authority or "
            "on their behalf to assist in researching and interpreting "
            "facts and the law and in applying the law to a concrete set "
            "of facts; or to influence the outcome of an election or "
            "referendum or the voting behaviour of natural persons."
        ),
        examples=(
            "legaltech portco's case-outcome predictor",
            "political-microtargeting platform",
        ),
    ),
)


# Public, immutable lookup keyed by snake_case use-case identifier.
ANNEX_III_BY_KEY: Mapping[str, AnnexIIICategory] = {c.key: c for c in _CATEGORIES}

# Ordered list of all Annex III categories (for enumeration / display).
ANNEX_III_CATEGORIES: tuple[AnnexIIICategory, ...] = _CATEGORIES


# Use-case keys that are NOT high-risk by Annex III but appear frequently
# in PE portfolios — kept here so the tool can render a verdict + a
# transparency note (Article 50) instead of forcing users into a
# high-risk-shaped doc pack.
NON_HIGH_RISK_HINTS: Mapping[str, str] = {
    "marketing_personalization": (
        "Marketing personalization / recommender systems do not appear in "
        "Annex III. Such systems may still trigger Article 50 transparency "
        "obligations if they generate or manipulate content."
    ),
    "fraud_detection": (
        "Annex III §5(b) expressly excludes AI systems used for the sole "
        "purpose of detecting financial fraud from the high-risk credit "
        "category."
    ),
    "internal_productivity": (
        "Internal productivity tooling (e.g. meeting summarisation, code "
        "completion) is generally not high-risk under Annex III. Article "
        "50 transparency obligations may apply if outputs are surfaced to "
        "third parties."
    ),
    "saas_analytics": (
        "B2B analytics, dashboards, and BI tooling are generally not "
        "high-risk under Annex III where they do not feed decisions on "
        "natural persons in any of the eight enumerated areas."
    ),
    "supply_chain": (
        "Supply-chain optimisation that does not touch critical "
        "infrastructure (Annex III §2) is generally not high-risk."
    ),
}


def get_category(key: str) -> AnnexIIICategory | None:
    """Look up an Annex III category by its snake_case key. Returns None
    if the key is not in the high-risk list."""
    return ANNEX_III_BY_KEY.get(key)


def list_keys() -> tuple[str, ...]:
    """Return all valid high-risk use-case keys, in regulation order."""
    return tuple(c.key for c in _CATEGORIES)
