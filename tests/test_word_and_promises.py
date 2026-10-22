"""S91/S92: writing into Word for real, and no more promised or acted-out
actions. Every reply below is from the live log of 2026-09-28. No real Word
is started: a fake COM object stands in."""
import asyncio

from brain.connectors import office
from brain.llm_router import truth_guard


# ── Word ────────────────────────────────────────────────────────────────
class FakeRange:
    def __init__(self, doc):
        self.doc = doc

    @property
    def Text(self):
        return self.doc.text + "\r"

    def InsertAfter(self, text):
        self.doc.text += text


class FakeDoc:
    def __init__(self, name, path="", text=""):
        self.Name, self.Path, self.text, self.saved_as = name, path, text, None

    @property
    def Content(self):
        return FakeRange(self)

    def Activate(self):
        pass

    def SaveAs2(self, path, fmt):
        self.saved_as = path


class FakeDocs:
    def __init__(self, app):
        self.app = app

    @property
    def Count(self):
        return len(self.app.docs)

    def Add(self):
        d = FakeDoc(f"Document{len(self.app.docs) + 1}")
        self.app.docs.append(d)
        self.app.ActiveDocument = d
        return d


class FakeWord:
    def __init__(self, docs):
        self.docs, self.Visible = list(docs), False
        self.ActiveDocument = self.docs[-1] if self.docs else None
        self.Documents = FakeDocs(self)


class FakeClient:
    def __init__(self, app):
        self.app = app

    def GetActiveObject(self, name):
        return self.app

    def Dispatch(self, name):
        return self.app


class FakePythoncom:
    def CoInitialize(self):
        pass

    def CoUninitialize(self):
        pass


def _word(monkeypatch, docs, last_reply=""):
    app = FakeWord(docs)
    monkeypatch.setattr(office, "_com", lambda: (FakeClient(app), FakePythoncom()))
    monkeypatch.setattr(office, "_last_reply", lambda: last_reply)
    return app


def test_writes_into_the_new_unsaved_document_in_front(monkeypatch):
    fresh = FakeDoc("Document2")
    _word(monkeypatch, [fresh])
    out = office.write_in_word({"source": "text", "text": "There was a robot named MBUDDY."})
    assert "There was a robot named MBUDDY." in fresh.text
    assert out.startswith('Wrote 6 words into "Document2"') and "isn't saved yet" in out


def test_never_writes_into_a_saved_file(monkeypatch):
    report = FakeDoc("Mobile Coverage Internship Report final.docx", path=r"C:\Users\example\Documents", text="Report")
    app = _word(monkeypatch, [report])
    office.write_in_word({"source": "text", "text": "A story."})
    assert report.text == "Report"                        # untouched
    assert app.ActiveDocument is not report and "A story." in app.ActiveDocument.text


def test_last_reply_is_written_without_tags_or_stage_directions(monkeypatch):
    fresh = FakeDoc("Document2")
    _word(monkeypatch, [fresh], last_reply="[FOCUSED] *smiles* Okay, here's a short story. The end.")
    office.write_in_word({"source": "your_last_reply"})
    assert fresh.text.strip() == "Okay, here's a short story. The end."


def test_nothing_to_write_is_a_failure(monkeypatch):
    _word(monkeypatch, [FakeDoc("Document2")], last_reply="")
    assert str(office.write_in_word({"source": "your_last_reply"})).startswith("FAILED:")


def test_save_as_never_overwrites(monkeypatch, tmp_path):
    (tmp_path / "story.docx").write_text("old", encoding="utf-8")
    fresh = FakeDoc("Document2")
    _word(monkeypatch, [fresh])
    monkeypatch.setattr(office, "_base_dir", lambda: tmp_path)
    out = office.write_in_word({"source": "text", "text": "Hello there.", "save_as": "story"})
    assert fresh.saved_as.endswith("story (2).docx") and "Saved as" in out


# ── promises and acted-out actions ──────────────────────────────────────
def run(chunks, tools_ran=False):
    async def gen():
        for c in chunks:
            yield c

    async def go():
        return "".join([d async for d in truth_guard(gen(), tools_ran=tools_ran, memory_text="")]).strip()

    return asyncio.run(go())


def test_promises_with_nothing_behind_them_are_dropped():
    assert run(['[HAPPY] Let\'s play "Wake Me Up". I\'ll start it right now.']) == '[HAPPY] Let\'s play "Wake Me Up".'
    # Only a filler would remain, so the honest line replaces it.
    assert run(["[NORMAL] Got it. I'll save that story right into the Word file you opened."]) == "[NORMAL] Nothing changed just now."
    assert run(["[NORMAL] Alright, I'm adding it now."]) == "[NORMAL] Nothing changed just now."


def test_acted_out_actions_are_cut():
    assert run(["[NORMAL] *plays*"], tools_ran=True) == "[NORMAL]"
    assert run(["[NORMAL] *types gently* There was a robot named MBUDDY."]) == "[NORMAL] There was a robot named MBUDDY."


def test_offers_and_real_reports_stay():
    assert run(["[NORMAL] Want me to add a reminder?"]) == "[NORMAL] Want me to add a reminder?"
    assert run(['[HAPPY] Wrote 40 words into "Document2" in Word.'], tools_ran=True) == \
        '[HAPPY] Wrote 40 words into "Document2" in Word.'
