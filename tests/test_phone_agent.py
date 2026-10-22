"""Tests for api/phone_agent_api.py: the step the brain gives the phone is well formed, or refused."""
import json

from api import phone_agent_api as pa
from api import remote_gateway as gw


def test_gateway_lets_a_paired_phone_ask_for_a_step_and_nothing_near_it():
    assert gw.allowed("POST", "ui/phone-agent/step")
    assert not gw.allowed("GET", "ui/phone-agent/step")
    assert not gw.allowed("POST", "ui/phone-agent/other")


def test_parse_plain_json_and_json_in_words():
    assert pa.parse_action('{"do":"tap","id":7}') == {"do": "tap", "id": 7}
    out = pa.parse_action('Sure! Here is the step:\n```json\n{"do":"type","id":3,"text":"hello {there}","say":"Typing it"}\n```')
    assert out == {"do": "type", "id": 3, "text": "hello {there}", "say": "Typing it"}


def test_parse_refuses_what_is_not_a_step():
    for bad in ["", "no json here", "{", '{"do":"format_disk"}', '{"do":"tap"}', '{"do":"tap","id":-1}', '{"do":"tap","id":"x"}',
                '{"do":"open"}', '{"do":"open_url","url":"javascript:alert(1)"}', '{"do":"open_url","url":"file:///sdcard/x"}',
                '{"do":"open_url","url":"intent://x#Intent;end"}', "[1,2]"]:
        assert pa.parse_action(bad) is None, bad


def test_parse_allows_the_safe_link_kinds_only():
    for url in ["https://wa.me/96170123456?text=hi", "tel:+96170123456", "smsto:+96170123456?body=hi", "mailto:a@b.c", "geo:0,0?q=Hamra"]:
        assert pa.parse_action(json.dumps({"do": "open_url", "url": url}))["url"] == url


def test_parse_fills_a_spoken_line_for_the_endings_and_trims_long_text():
    assert pa.parse_action('{"do":"done"}')["say"] == "Done."
    assert pa.parse_action('{"do":"fail"}')["say"]
    assert len(pa.parse_action(json.dumps({"do": "type", "id": 1, "text": "x" * 5000}))["text"]) == 600
    assert pa.parse_action('{"do":"scroll","dir":"UP"}')["dir"] == "up"
    assert pa.parse_action('{"do":"scroll"}')["dir"] == "down"


def test_messages_carry_the_task_the_steps_and_the_screen_and_are_bounded():
    msgs = pa.build_messages("send hi to Sara", "[1] button \"Send\"", [{"did": "opened WhatsApp", "result": "ok"}], "WhatsApp")
    assert msgs[0]["role"] == "system" and "ONE JSON object" in msgs[0]["content"]
    body = msgs[1]["content"]
    assert "send hi to Sara" in body and "opened WhatsApp" in body and "[1] button" in body and "WhatsApp" in body
    huge = pa.build_messages("g" * 5000, "s" * 50000, [{"did": "d" * 5000}] * 40)
    assert len(huge[1]["content"]) < 14000


def test_a_question_carries_its_own_choices_or_none():
    out = pa.parse_action('{"do":"ask","say":"Send it?","options":["Yes","No"]}')
    assert out["options"] == ["Yes", "No"]
    assert "options" not in pa.parse_action('{"do":"ask","say":"What colour?"}')
    assert "options" not in pa.parse_action('{"do":"ask","say":"x","options":[]}')
    assert pa.parse_action('{"do":"ask","say":"x","options":["a","b","c","d","e","f"]}')["options"] == ["a", "b", "c", "d"]
    assert "options" not in pa.parse_action('{"do":"tap","id":1,"options":["x"]}')


def test_which_brain_plans_the_step(tmp_path, monkeypatch):
    pick = tmp_path / "pick.txt"
    monkeypatch.setattr(pa, "LOCAL_PICK", pick)
    assert pa.chosen_brain("") == ("cloud", "")
    pick.write_text("local:qwen3:8b", encoding="utf-8")
    assert pa.chosen_brain("") == ("local", "qwen3:8b")
    assert pa.chosen_brain("cloud") == ("cloud", "")          # what the phone asks for wins over the file
    assert pa.chosen_brain("local") == ("local", "")
