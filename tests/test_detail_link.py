import json
from telegram_project_radar.inbox import twenty_rich_text_summary


def test_rvc_link_preserves_visible_source_in_one_block():
    text = "Вакансия\n👉 Контакты и полное описание\n"
    url = "https://app.rvc.global/vacancy/view/example-123?utm_source=telegram"
    result = twenty_rich_text_summary(text, candidate_id="example", detail_url=url)
    blocks = json.loads(result["blocknote"])
    assert len(blocks) == 1
    content = blocks[0]["content"]
    assert content[1]["type"] == "link" and content[1]["href"] == url
    assert content[0]["text"] + content[1]["content"][0]["text"] + content[2]["text"] == text
    assert f"[👉 Контакты и полное описание]({url})" in result["markdown"]


def test_untrusted_link_scheme_is_not_embedded():
    text = "👉 Контакты и полное описание"
    result = twenty_rich_text_summary(text, candidate_id="example", detail_url="javascript:alert(1)")
    assert result["markdown"] == text
    assert json.loads(result["blocknote"])[0]["content"][0]["type"] == "text"
