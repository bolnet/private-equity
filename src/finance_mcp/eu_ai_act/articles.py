"""
articles — Frozen Article 9–15 (and Article 6, 50) requirement schemas
from Regulation (EU) 2024/1689 (the "AI Act").

Source of record (public, verifiable):
  EUR-Lex, CELEX:32024R1689.
  https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32024R1689

Each ArticleRequirement below freezes the documentation skeleton an
operator must produce to evidence compliance. Wording is a faithful
summary of the published regulation, not a verbatim copy — auditors
should compare line for line against EUR-Lex.

Effective dates frozen here come directly from Article 113 of the
regulation:

  - 2 February 2025  — Chapter I (general) and Chapter II (prohibited
                       practices) entered into application.
  - 2 August 2025    — Chapters III §4 (notifying authorities), V
                       (general-purpose AI models), VII (governance),
                       XII (penalties, except Article 101).
  - 2 August 2026    — Remainder of the regulation, including the
                       high-risk obligations of Articles 6, 9–15. THIS
                       IS THE BINDING DEADLINE FOR HIGH-RISK SYSTEMS.
  - 2 August 2027    — Article 6(1) high-risk classification for
                       products covered by Union harmonisation legislation
                       listed in Annex I.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


REGULATION_CITATION: str = (
    "Regulation (EU) 2024/1689 of the European Parliament and of the "
    "Council of 13 June 2024 (Artificial Intelligence Act)."
)

# Binding deadline for the high-risk obligations of Articles 9–15.
HIGH_RISK_DEADLINE: date = date(2026, 8, 2)

# Annex I product-related high-risk classification deadline.
HIGH_RISK_DEADLINE_ANNEX_I: date = date(2027, 8, 2)

# General-purpose AI model obligations effective date.
GPAI_DEADLINE: date = date(2025, 8, 2)


@dataclass(frozen=True)
class ArticleRequirement:
    """Documentation skeleton for a single Article requirement.

    Attributes:
        article:        E.g. "Article 9".
        title:          Human label.
        summary:        Faithful summary of the regulation text.
        deliverables:   The named documentation artefacts the operator
                        must produce / maintain.
        effective_date: When the obligation begins to bind.
    """

    article: str
    title: str
    summary: str
    deliverables: tuple[str, ...]
    effective_date: date


ARTICLE_6: ArticleRequirement = ArticleRequirement(
    article="Article 6",
    title="Classification rules for high-risk AI systems",
    summary=(
        "An AI system is high-risk if it is intended to be used as a "
        "safety component of a product covered by Union harmonisation "
        "legislation listed in Annex I AND that product is required to "
        "undergo third-party conformity assessment; OR if it is listed "
        "in Annex III. Article 6(3) provides a narrow derogation: an "
        "Annex III system is not high-risk where it does not pose a "
        "significant risk of harm to health, safety, or fundamental "
        "rights, including by not materially influencing the outcome of "
        "decision-making."
    ),
    deliverables=(
        "Classification verdict (high-risk / not high-risk) with citation "
        "to Annex III paragraph or to Article 6(3) derogation reasoning.",
        "Where Article 6(3) derogation is invoked: documented justification "
        "kept on file and registered in the EU database (Article 49(2)).",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_9: ArticleRequirement = ArticleRequirement(
    article="Article 9",
    title="Risk management system",
    summary=(
        "Providers of high-risk AI systems shall establish, implement, "
        "document, and maintain a continuous, iterative risk management "
        "system across the entire lifecycle. It must (a) identify and "
        "analyse known and reasonably foreseeable risks to health, "
        "safety, and fundamental rights; (b) estimate and evaluate "
        "post-market and foreseeable-misuse risks; (c) adopt targeted "
        "risk-management measures; and (d) verify residual risk is "
        "acceptable."
    ),
    deliverables=(
        "Per-system risk register listing identified hazards, likelihood, "
        "severity, affected fundamental rights, and mitigation owner.",
        "Residual-risk acceptance memo signed by accountable executive.",
        "Plan for ongoing post-market monitoring feedback into the register.",
        "Evidence the register is reviewed at least annually and on each "
        "material change to the system.",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_10: ArticleRequirement = ArticleRequirement(
    article="Article 10",
    title="Data and data governance",
    summary=(
        "Training, validation, and testing datasets shall be subject to "
        "data governance and management practices appropriate to the "
        "intended purpose. Datasets shall be relevant, sufficiently "
        "representative, and to the best extent possible free of errors "
        "and complete in view of the intended purpose. Bias examination "
        "and mitigation are required."
    ),
    deliverables=(
        "Data sourcing and provenance log.",
        "Bias examination report (protected attributes covered).",
        "Representativeness statement against the intended population.",
        "Data preparation, labelling, and cleansing procedure document.",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_11: ArticleRequirement = ArticleRequirement(
    article="Article 11",
    title="Technical documentation",
    summary=(
        "Technical documentation shall be drawn up before placing on the "
        "market or putting into service and kept up to date. It shall "
        "demonstrate that the high-risk AI system complies with the "
        "requirements of Section 2. The minimum content is set out in "
        "Annex IV."
    ),
    deliverables=(
        "Annex IV §1 — General description: intended purpose, provider, "
        "version, hardware on which the system is intended to run.",
        "Annex IV §2 — Detailed description: methods, design choices, "
        "system architecture, key design rationales.",
        "Annex IV §3 — Monitoring and control: human oversight measures, "
        "technical measures for output interpretation.",
        "Annex IV §4 — Performance metrics: accuracy, robustness, and "
        "cybersecurity metrics with measurement methodology.",
        "Annex IV §5 — Risk management system per Article 9.",
        "Annex IV §6 — Lifecycle changes log.",
        "Annex IV §7 — Standards applied or, where harmonised standards "
        "have not been applied, the technical solutions adopted.",
        "Annex IV §8 — EU declaration of conformity.",
        "Annex IV §9 — Post-market monitoring plan per Article 72.",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_12: ArticleRequirement = ArticleRequirement(
    article="Article 12",
    title="Record-keeping (logs)",
    summary=(
        "High-risk AI systems shall technically allow for the automatic "
        "recording of events ('logs') over the lifetime of the system. "
        "Logs shall ensure a level of traceability appropriate to the "
        "intended purpose."
    ),
    deliverables=(
        "Logging schema specification (events, retention, access).",
        "Log-retention policy aligned to Article 19 (≥ 6 months).",
        "Log-integrity protections (tamper-evident storage).",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_13: ArticleRequirement = ArticleRequirement(
    article="Article 13",
    title="Transparency and provision of information to deployers",
    summary=(
        "High-risk AI systems shall be designed and developed so their "
        "operation is sufficiently transparent to enable deployers to "
        "interpret the system's output and use it appropriately. They "
        "shall be accompanied by instructions for use containing "
        "concise, complete, correct, and clear information."
    ),
    deliverables=(
        "Instructions for use document covering: provider identity and "
        "contact, characteristics, capabilities and limitations of "
        "performance, foreseeable circumstances which may lead to risks, "
        "performance regarding specific persons or groups, input data "
        "specifications, hardware/software needed for proper operation, "
        "human oversight measures, expected lifetime, maintenance and "
        "care measures, log-collection mechanisms.",
        "Plain-language summary of capabilities and limitations.",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_14: ArticleRequirement = ArticleRequirement(
    article="Article 14",
    title="Human oversight",
    summary=(
        "High-risk AI systems shall be designed and developed in such a "
        "way, including with appropriate human-machine interface tools, "
        "that they can be effectively overseen by natural persons "
        "during the period in which they are in use. Oversight measures "
        "shall enable persons to understand the relevant capacities and "
        "limitations, remain aware of automation bias, correctly "
        "interpret output, decide not to use the output, and intervene "
        "or interrupt operation through a 'stop' button."
    ),
    deliverables=(
        "Human oversight design memo: who oversees, what they see, what "
        "they can override, with what latency.",
        "Operator training plan and competency criteria.",
        "Stop-button / override mechanism specification.",
        "Automation-bias mitigation: review-rate sampling, calibration "
        "exercises, dissent capture.",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_15: ArticleRequirement = ArticleRequirement(
    article="Article 15",
    title="Accuracy, robustness and cybersecurity",
    summary=(
        "High-risk AI systems shall be designed and developed in such a "
        "way that they achieve an appropriate level of accuracy, "
        "robustness, and cybersecurity, and that they perform "
        "consistently in those respects throughout their lifecycle. "
        "Performance levels shall be declared in the instructions for "
        "use. Resilience must address errors, faults, inconsistencies, "
        "feedback loops, and adversarial attacks (data poisoning, "
        "model poisoning, evasion, confidentiality)."
    ),
    deliverables=(
        "Accuracy metrics with measurement methodology and reference dataset.",
        "Robustness test report (perturbation, drift, edge case).",
        "Adversarial / red-team assessment report.",
        "Cybersecurity controls aligned to NIS2 / ISO 27001 / SOC 2.",
        "Incident-response plan with regulator-notification path "
        "(Article 73 serious-incident reporting).",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


ARTICLE_50: ArticleRequirement = ArticleRequirement(
    article="Article 50",
    title="Transparency obligations for providers and deployers of certain AI systems",
    summary=(
        "Providers shall ensure AI systems intended to interact directly "
        "with natural persons are designed so that those persons are "
        "informed they are interacting with an AI system. Deepfakes and "
        "AI-generated text on matters of public interest must be "
        "disclosed. Emotion-recognition and biometric-categorisation "
        "deployers must inform exposed persons."
    ),
    deliverables=(
        "User-facing disclosure that the system is AI-driven.",
        "Watermarking / provenance signal for AI-generated synthetic content.",
        "Plain-language notice attached to deployments touching natural persons.",
    ),
    effective_date=HIGH_RISK_DEADLINE,
)


# Articles addressed by the audit pack, in regulation order.
HIGH_RISK_ARTICLES: tuple[ArticleRequirement, ...] = (
    ARTICLE_6,
    ARTICLE_9,
    ARTICLE_10,
    ARTICLE_11,
    ARTICLE_12,
    ARTICLE_13,
    ARTICLE_14,
    ARTICLE_15,
)


# Articles that apply when the system is NOT high-risk but still falls
# under the AI Act (typically via Article 50).
LIMITED_RISK_ARTICLES: tuple[ArticleRequirement, ...] = (ARTICLE_50,)
