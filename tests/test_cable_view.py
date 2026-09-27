from engineering.instructions import cable_view as V
def test_unknown_lengths_and_actions_are_visible_and_html_is_escaped():
    report={"status":"ACTION_REQUIRED","issues":[],
        "cables":[{"id":"<wire>","status":"INCOMPLETE","route_length_mm":None,
        "planning_length_mm":None,"cut_length_mm":None,"issues":[],
        "unknowns":{"points":{"value":None,"obtained_by":"Измерить <контакт>"}}}]}
    page=V.render(report,[{"id":"<wire>","source":"A","target":"B","net":"GND"}])
    assert "&lt;wire&gt;" in page and "Измерить &lt;контакт&gt;" in page
    assert "Не определена" in page and "Для отрезания" in page
    assert "<wire>" not in page

