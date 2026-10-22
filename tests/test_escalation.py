import pytest

from brain import escalation


@pytest.mark.parametrize("said, task", [
    ("use the bigger model to explain why the sky is blue", "explain why the sky is blue"),
    ("Ask the cloud model about the best order for my meetings", "the best order for my meetings"),
    ("please use the bigger brain and compare these two plans", "compare these two plans"),
    ("can you ask the bigger model: what is a good name for a dog", "what is a good name for a dog"),
    ("think harder about how to split the bill fairly", "how to split the bill fairly"),
])
def test_naming_the_bigger_model_hands_the_task_to_it(said, task):
    assert escalation.explicit(said) == task


@pytest.mark.parametrize("said", [
    "what is the bigger model you use", "the cloud is cloudy today", "use the music app", "ask me anything",
    "use the bigger model", "I use a bigger monitor now", "think about it",
])
def test_other_words_are_left_to_the_models_own_choice(said):
    assert escalation.explicit(said) is None


def test_a_failure_is_said_in_words(monkeypatch):
    from core import cloud
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    ok, why = escalation.run("explain tides")
    assert ok is False and "key" in why.lower()


@pytest.mark.parametrize("task, source", [
    ("summarise what's on my clipboard", "clipboard"),
    ("explain what is on my screen", "screen"),
    ("what does this screenshot show", "screen"),
    (r"summarise C:\Users\Sam\Documents\contract.docx", r"C:\Users\Sam\Documents\contract.docx"),
    ("read ~/Documents/notes.txt and list the actions", "~/Documents/notes.txt"),
    ("explain why the sky is blue", ""),
])
def test_a_task_that_names_something_from_the_pc_is_recognised(task, source):
    assert escalation.source_of(task) == source
