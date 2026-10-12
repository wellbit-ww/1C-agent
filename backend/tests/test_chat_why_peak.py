"""Follow-up «почему пики?» не должен уходить в бесполезный period-график."""
from services.chat_service import _normalize_actions, _wants_why_peak


def test_why_peak_detected():
    assert _wants_why_peak("А за конкретно этот квартал, почему такие пики?")
    assert _wants_why_peak("Почему такой рост в июле?")
    assert not _wants_why_peak("Построй график по кварталам")


def test_why_peak_remaps_chart_to_general():
    actions = _normalize_actions(
        [{"action": "chart", "chart_type": "line", "period": "quarter"}],
        "почему такие пики в этом квартале",
    )
    assert actions == [{"action": "general"}]
