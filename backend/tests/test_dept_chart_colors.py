from services.dept_chart_colors import department_chart_color


def test_department_colors_are_stable_and_distinct():
    assert department_chart_color("СТО") == "#70AD47"
    assert department_chart_color("Сервис") == "#17A2B8"
    assert department_chart_color("СС") == department_chart_color("Сервис")
    assert department_chart_color("СТО") != department_chart_color("Сервис")
    assert department_chart_color("Совтест") == "#C00000"
