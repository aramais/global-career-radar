import pytest

from job_intake.annotation.text import source_units
from job_intake.models.job import JobRecord
from job_intake.scoring.evidence import description_evidence


def _record(text, **kwargs):
    return JobRecord(
        source="test", company="Example", title="Analytics Manager",
        original_url="https://example.com/jobs/analytics", description_clean=text, **kwargs,
    )


def test_only_current_duties_can_supply_fit_evidence():
    record = _record(
        "About us\nWe lead pricing experiments worldwide.\n"
        "Responsibilities\nYou will manage cross-functional projects without direct reports.\n"
        "Run experiment design and translate results into commercial decisions.\n"
        "Requirements\nExperience in monetization and causal inference.\n"
        "Benefits\nWe offer pricing courses and leadership training.\nApply now"
    )
    evidence = description_evidence(record)
    assert evidence.project_leadership
    assert evidence.substantive_decision
    assert evidence.matched_weights({
        "experimentation": 3, "business decisions": 3, "project leadership": 3,
        "monetization": 3, "causal inference": 3, "pricing": 3,
    }) == [("experimentation", 3), ("business decisions", 3), ("project leadership", 3)]
    assert "Experience" not in evidence.duties_text
    assert "We offer" not in evidence.duties_text
    units = {unit["unit_id"]: unit["text"] for unit in source_units(record)}
    assert evidence.signals
    assert all(signal["quote"] in units[signal["unit_id"]] for signal in evidence.signals)


@pytest.mark.parametrize("text", [
    "You may lead experiments and manage projects.",
    "You might manage projects and run experiments.",
    "Do not manage projects or run experiments.",
    "You will not manage teams or run experiments.",
    "You will lead experiments if needed.",
    "Will you own business decisions and lead projects?",
    "Experience managing teams and leading experiments is preferred.",
])
def test_uncertain_negated_and_past_scope_are_not_positive_duties(text):
    evidence = description_evidence(_record("Responsibilities\n" + text))
    assert not evidence.project_leadership
    assert not evidence.analytical_mandate
    assert not evidence.substantive_decision
    assert evidence.matched_weights({"experimentation": 3, "project leadership": 3}) == []


def test_titles_and_collaborators_do_not_establish_project_ownership():
    evidence = description_evidence(_record(
        "Responsibilities\nWork with project managers who lead analytics teams.\n"
        "Partner with the Head of Decision Science to present findings.\n"
        "Collaborate with leaders who own strategic decisions."
    ))
    assert not evidence.project_leadership
    assert not evidence.substantive_decision
    assert evidence.matched_weights({"decision science": 3, "project leadership": 3}) == []


def test_individual_contributor_can_explicitly_lead_projects():
    evidence = description_evidence(_record(
        "Responsibilities\nAs an individual contributor, you will own analytical projects "
        "and coordinate cross-functional teams without direct reports.\n"
        "Lead pricing experiments and recommend changes to product strategy."
    ))
    assert evidence.project_leadership
    assert evidence.analytical_mandate
    assert evidence.substantive_decision


def test_reporting_outputs_do_not_establish_independent_decision_mandate():
    evidence = description_evidence(_record(
        "Responsibilities\nLead business reporting initiatives.\n"
        "Design and build comprehensive reports that guide business decisions.\n"
        "Present insights through dashboards to facilitate informed decision-making.\n"
        "Analyze market trends to keep reporting relevant."
    ))
    assert evidence.reporting_core
    assert not evidence.substantive_decision
    assert not evidence.analytical_mandate
    assert evidence.matched_weights({"business decisions": 3}) == []


def test_reports_do_not_penalize_an_independent_experimentation_mandate():
    evidence = description_evidence(_record(
        "Responsibilities\nBuild reports for stakeholders.\n"
        "Lead pricing experiments and use the results to influence commercial decisions."
    ))
    assert not evidence.reporting_core
    assert evidence.analytical_mandate
    assert evidence.substantive_decision
    assert evidence.matched_weights({
        "pricing": 3, "experimentation": 3, "business decisions": 3,
    }) == [("pricing", 3), ("experimentation", 3), ("business decisions", 3)]


def test_synonym_repetitions_use_configured_weights_once():
    evidence = description_evidence(_record(
        "Responsibilities\nLead experiments and experimentation.\n"
        "Run experiment design and make strategic decisions based on results."
    ))
    weights = {"experimentation": 4, "business decisions": 2}
    assert evidence.matched_weights(weights) == [("experimentation", 4), ("business decisions", 2)]
    signals = list(evidence.signals)
    assert evidence.matched_weights(weights) == [("experimentation", 4), ("business decisions", 2)]
    assert evidence.signals == signals


def test_unsectioned_duties_survive_but_candidate_skills_and_benefits_do_not():
    evidence = description_evidence(_record(
        "The manager leads a small data team.\n"
        "Lead analytical projects and own the roadmap.\n"
        "EDUCATION & EXPERIENCE\nExperience in pricing and experiments.\n"
        "SKILLS & CERTIFICATIONS\nProject management and causal inference.\n"
        "What we offer our team members: Lead pricing workshops with our training budget."
    ))
    assert evidence.project_leadership
    assert evidence.matched_weights({"pricing": 3, "causal inference": 3}) == []


def test_html_heading_scope_keeps_quotes_from_the_original_source():
    evidence = description_evidence(_record(
        "Responsibilities Lead commercial analytics projects. "
        "Requirements Run experiments and own pricing decisions.",
        description_raw=(
            "<h2>Responsibilities</h2><p>Lead commercial analytics projects.</p>"
            "<h2>Requirements</h2><p>Run experiments and own pricing decisions.</p>"
        ),
    ))
    assert evidence.project_leadership
    assert evidence.matched_weights({"pricing": 3, "experimentation": 3}) == []


def test_unknown_scope_does_not_invent_mandate_or_company_preferences():
    evidence = description_evidence(_record(
        "Our company builds products for a global marketplace. "
        "We offer a high-growth startup environment."
    ))
    assert not evidence.project_leadership
    assert not evidence.analytical_mandate
    assert not evidence.substantive_decision
    assert not evidence.reporting_core
    assert evidence.matched_weights({"marketplace": 2, "pricing": 3}) == []


@pytest.mark.parametrize("duty", [
    "Lead pricing, monetization and experimentation projects with business teams.",
    "Lead decision support, budget planning and forecasting with Finance.",
])
def test_explicit_economic_function_leadership_establishes_substantive_scope(duty):
    evidence = description_evidence(_record(duty))
    assert evidence.project_leadership
    assert evidence.analytical_mandate
    assert evidence.substantive_decision


def test_economic_words_on_infrastructure_do_not_invent_decision_responsibility():
    evidence = description_evidence(_record("Build a pricing data pipeline for analysts."))
    assert not evidence.substantive_decision


def test_applied_methods_and_project_leadership_do_not_need_direct_reports():
    evidence = description_evidence(_record(
        "Apply causal inference and experimentation to pricing, monetization and unit "
        "economics to inform business decisions. Lead cross-functional projects and "
        "manage stakeholders. This role has no direct reports."
    ))
    assert evidence.project_leadership
    assert evidence.analytical_mandate
    assert evidence.substantive_decision


def test_shared_decision_responsibility_is_not_a_collaborators_job_title():
    evidence = description_evidence(_record(
        "Responsibilities\nPartner with senior Product and Commercial leaders to "
        "define priorities, shape roadmaps, and inform strategic decisions."
    ))
    assert evidence.substantive_decision
    assert evidence.matched_weights({"business decisions": 3}) == [("business decisions", 3)]


def test_possessive_own_does_not_invent_project_leadership():
    evidence = description_evidence(_record(
        "Responsibilities\nBuild the infrastructure with external tools or our own, "
        "so testing can spread across the product teams."
    ))
    assert not evidence.project_leadership


def test_inline_you_will_heading_recovers_duties_without_importing_requirements():
    evidence = description_evidence(_record(
        "Our mission: Build better products. You will: own marketing measurement; "
        "lead pricing experiments and manage a team. "
        "We'll have a match if you: Experience in causal inference and monetization.",
        description_raw=(
            "<strong>Our mission:</strong> Build better products. "
            "<p>You will: own marketing measurement; lead pricing experiments and manage "
            "a team.</p><p>We'll have a match if you: Experience in causal inference "
            "and monetization.</p>"
        ),
    ))
    assert evidence.project_leadership
    assert evidence.analytical_mandate
    assert evidence.matched_weights({"causal inference": 3, "monetization": 3}) == []


def test_unrecognized_candidate_heading_does_not_leave_requirements_as_duties():
    evidence = description_evidence(_record(
        "Responsibilities\nLead analytics projects and manage a team.\n"
        "Skills & Experience Experience: Extensive experience in data science.\n"
        "Scaling Impact: Experience scaling analytics and experimenting with pricing.\n"
        "Experience building and scaling experimentation platforms."
    ))
    assert evidence.project_leadership
    assert "Scaling Impact" not in evidence.duties_text
    assert evidence.matched_weights({"pricing": 3, "experimentation": 3}) == []


@pytest.mark.parametrize("history", [
    "Experience building and scaling experimentation platforms.",
    "Scaling Impact: Experience scaling analytics and leading pricing projects.",
    "Technical Expertise: Deep hands-on experience developing pricing models.",
    "You have experience conducting experimentation and managing analytics projects.",
])
def test_candidate_history_is_excluded_even_if_source_labels_it_as_role(history):
    evidence = description_evidence(_record("Responsibilities\n" + history))
    assert not evidence.project_leadership
    assert not evidence.analytical_mandate
    assert evidence.matched_weights({"pricing": 3, "experimentation": 3}) == []


@pytest.mark.parametrize("duty", [
    "Lead pricing experiments and build reports for stakeholders.",
    "Build reports for stakeholders and design pricing experiments.",
])
def test_independent_experiments_survive_reporting_in_the_same_sentence(duty):
    evidence = description_evidence(_record("Responsibilities\n" + duty))
    assert evidence.analytical_mandate
    assert evidence.substantive_decision
    assert not evidence.reporting_core
    assert evidence.matched_weights({"experimentation": 3}) == [("experimentation", 3)]


def test_work_with_finance_on_economic_decisions_is_actual_role_participation():
    evidence = description_evidence(_record(
        "Work with the general manager and Finance on P&L and business decisions."
    ))
    assert evidence.substantive_decision
    assert not evidence.project_leadership
    assert evidence.matched_weights({
        "general manager": 3, "p&l": 3, "business decisions": 3,
    }) == [("general manager", 3), ("p&l", 3), ("business decisions", 3)]


@pytest.mark.parametrize("duty", [
    "Work with managers who own business decisions and lead analytics projects.",
    "Work with the general manager who owns business decisions and leads the team.",
    "Work with Finance colleagues who lead pricing projects and own budget decisions.",
])
def test_work_with_others_does_not_assign_their_ownership_to_this_role(duty):
    evidence = description_evidence(_record("Responsibilities\n" + duty))
    assert not evidence.project_leadership
    assert not evidence.substantive_decision
    assert evidence.matched_weights({
        "project leadership": 3, "business decisions": 3,
    }) == []


def test_optional_finance_participation_cannot_supply_confirmed_scope():
    evidence = description_evidence(_record(
        "Responsibilities\nYou may work with Finance on business decisions."
    ))
    assert not evidence.substantive_decision
    assert evidence.matched_weights({"business decisions": 3}) == []


def test_work_model_and_recruiting_title_do_not_become_actual_duties():
    heading = (
        "Work Model: Flexible hybrid or remote Position Overview We are seeking a "
        "senior-level Cybersecurity Data Science and Analytics Lead to help shape and "
        "scale how security data is used across the organization."
    )
    evidence = description_evidence(_record(
        heading + "\nResponsibilities\nLead projects independently and take ownership "
        "of solutions from initial problem definition through delivery."
    ))
    assert heading not in evidence.duties_text
    assert not evidence.analytical_mandate
    assert evidence.project_leadership
    assert all(heading != signal["quote"] for signal in evidence.signals)
