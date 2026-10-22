"""Did it work? The feedback loop that makes MBUDDY's notes earn their place.

Until now the brain wrote notes and used them, but only app jobs ever learned
whether the result was right. This module gives EVERY action turn a verdict and
a feedback chain, and turns mistakes into notes written for the MODEL (not for
the user):

1. OUTCOME. Each turn that ran a tool is recorded: what was asked, which tool,
   what came back, which notes were in the prompt.
2. VERDICT BY CODE first: a tool failure, a "NOT PLAYING", a missing confirmation
   are failures; a result that states the checked state ("Playing ... ", "window
   appeared: yes") is verified. Anything else is only "executed".
3. SELF JUDGE second, only for "executed": the local model is asked, with a
   fixed schema, whether the observed result satisfies what was asked. Its
   answer is stored as `judged`, never as verified, and moves a note's numbers
   only a little.
4. USER REACTION third and strongest: on the user's next message the model
   classifies it (thanks / correction / retry / continue / unrelated). A
   correction ("you already did that", "not that one") overrides any self-check.
5. CREDIT goes only to the notes that were really in the prompt, once.
6. A failure or correction writes or REVISES a lesson in the vault, in the form
   the model reads best: WHEN / DO / DON'T / BECAUSE / EVIDENCE. If a lesson for
   the same situation exists it is revised with the new evidence (the model is
   shown the old text), not duplicated. The lesson is written BEFORE any note is
   demoted, so a failing note is replaced by what to do instead, not just lost.

Everything is local. Model calls are small, use a schema, run in the
background, and wait for the model to be free (a turn in progress goes first).
`ask` can be replaced in tests.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import utils.logger as log

_lock = threading.RLock()
LESSON_KIND = "Debugging experience"       # the vault folder lessons live in
REACTIONS = ("thanks", "correction", "change", "retry", "continue", "unrelated")
REACTION_WINDOW_S = 5 * 60
JUDGE_GAP_S = 20
# Tools whose success text itself states the checked state (the tool looked).
VERIFIED_TOOLS = {"music_play", "add_reminder", "add_task", "complete_task", "change_reminder", "set_timer", "adjust_volume", "word_write",
                  "create_file", "open_app", "calendar_export_ics", "calendar_import_ics", "run_routine"}
_UNCERTAIN = re.compile(r"not confirmed|not within|no, not|isn't playing yet|NOT DONE YET|NEEDS CHOICE|unable to confirm", re.I)
_FAILED = re.compile(r"^(FAILED|NOT PLAYING|TOOL (FAILED|ERROR))", re.I)

_last: dict | None = None            # the latest outcome, waiting for the user's reaction
_last_judge = 0.0


def _path() -> Path:
    override = os.environ.get("MBUDDY_OUTCOMES_PATH")
    return Path(override) if override else Path.home() / ".buddy" / "outcomes.jsonl"


def _append(rec: dict) -> None:
    try:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent(limit: int = 200) -> list[dict]:
    out = []
    try:
        for line in _path().read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    except OSError:
        pass
    return out


# ── 1-2. the verdict by code ────────────────────────────────────────────────
def classify(tool: str, result) -> tuple[str, str]:
    """(verdict, reason): pass (verified) | executed (ran, not checked) | fail | unknown (waiting on the user)."""
    from brain.tools import ToolFailure
    text = str(result or "").strip()
    first = re.sub(r"^(FAILED:\s*)+", "", text.splitlines()[0] if text else "")[:200]
    if isinstance(result, ToolFailure) or _FAILED.match(text):
        if re.search(r"NEEDS CHOICE|switched off", text, re.I):
            return "unknown", first
        return "fail", first
    if _UNCERTAIN.search(text):
        return "unknown", first
    if tool in VERIFIED_TOOLS:
        return "pass", first
    return "executed", first


# ── 3-4. the model as judge and reader of the user's reaction ───────────────
def _loose(reply: str, schema: dict) -> dict | None:
    """The model sometimes answers in plain lines ("fail\nReason: ...", "category: retry\nissue: ...") instead of JSON, even when asked
    for it. Read that too, by the schema's own words: the first allowed value found names the enum field, "key: text" lines fill the
    rest. Anything not readable stays None, so a guess is never made up."""
    props = schema.get("properties", {})
    text = (reply or "").strip()
    if not text:
        return None
    # JSON in a code fence, or with words around it
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    enums = [k for k, v in props.items() if v.get("enum")]
    if not enums:
        # a schema of text fields only (the lesson): every required field must be there as its own "key: text" line,
        # by its name or a plain-English alias ("don't", "when"), so a field is never filled from another one
        out = {}
        for key in props:
            names = [re.escape(n) for n in _ALIASES.get(key, (key,))]
            m = re.search(r"(?im)^[\s*_#>\-]*(?:" + "|".join(names) + r")[\s*_]*[:=\-]\s*(.+)$", text)
            out[key] = m.group(1).strip().strip("*_ ") if m else ""
        need = schema.get("required") or list(props)
        return out if all(out.get(k) for k in need) else None
    out: dict = {}
    for key, spec in props.items():
        allowed = spec.get("enum")
        if allowed:
            m = re.search(r"\b(" + "|".join(re.escape(v) for v in allowed) + r")\b", text, re.IGNORECASE)
            if m:
                out[key] = next(v for v in allowed if v.lower() == m.group(1).lower())
    for key, spec in props.items():
        if key in out or spec.get("enum"):
            continue
        m = re.search(r"(?im)^\s*(?:" + re.escape(key) + r"|reason|issue|because|why)\s*[:=-]\s*(.+)$", text)
        out[key] = m.group(1).strip() if m else ""
    return out if any(out.get(k) for k in enums) else None


# the words a model uses for the lesson's fields when it answers in plain lines
_ALIASES = {"situation": ("situation", "when"), "do": ("do",), "dont": ("dont", "don't", "don’t", "do not"),
            "because": ("because", "why", "cause", "reason")}


def _ask(system: str, user: str, schema: dict, num_predict: int = 160) -> dict | None:
    import requests
    from brain.agent import _chat_model, _ollama_chat_url
    body = {"model": _chat_model(), "stream": False, "think": False, "format": schema,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": 0, "num_predict": num_predict}, "keep_alive": "30m"}
    try:
        reply = requests.post(_ollama_chat_url("background"), json=body, timeout=90).json().get("message", {}).get("content", "")
        try:
            data = json.loads(reply)
        except ValueError:
            return _loose(reply, schema)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        log.warn(f"[Outcome] model call failed ({type(exc).__name__}: {str(exc)[:80]}), reply began: {locals().get('reply', '')[:60]!r}")
        return None


JUDGE_SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["pass", "fail", "unknown"]},
                                                 "reason": {"type": "string"}}, "required": ["verdict", "reason"]}
REACTION_SCHEMA = {"type": "object", "properties": {"reaction": {"type": "string", "enum": list(REACTIONS)},
                                                    "issue": {"type": "string"}}, "required": ["reaction", "issue"]}
LESSON_SCHEMA = {"type": "object", "properties": {k: {"type": "string"} for k in ("situation", "do", "dont", "because")},
                 "required": ["situation", "do", "dont", "because"]}


def judge(rec: dict, ask=_ask) -> tuple[str, str]:
    """The self judge for an 'executed' outcome: does what came back satisfy what was asked?"""
    out = ask("You check whether a computer action did what the user asked, from what it reported. "
              "Say pass only if the report clearly shows the request was met. Say fail if it shows it was not. "
              "Say unknown if the report does not show either. Reason: under 15 words.",
              f"The user asked: {rec['asked']}\nThe action: {rec['tool']} {json.dumps(rec.get('args') or {}, ensure_ascii=False)[:200]}\n"
              f"It reported: {rec['result'][:400]}", JUDGE_SCHEMA, 160)
    if not out or out.get("verdict") not in ("pass", "fail", "unknown"):
        return "unknown", ""
    return out["verdict"], str(out.get("reason", ""))[:160]


# A change of mind or a question is not a correction: "make that every day" after a reminder set once is a new wish,
# and "are you sure?" asks; scored as corrections they wrote lessons that taught the opposite of what the user wants
# ("set repeat to daily, don't use once") and halved notes that were right (the field test and Sprint 1, 2026-10-19).
_WRONG = re.compile(r"\b(wrong|incorrect|not what i|that'?s not|isn'?t what|i (said|asked|told you)|you (forgot|missed|didn'?t)|"
                    r"didn'?t (work|do|happen)|not (it|that one|right)|mistake|messed up|broke|nothing happened|you already)\b", re.I)
_CHANGE = re.compile(r"^\W*(no,?\s+)?(actually|instead|rather|make (it|that|them)|change (it|that)|also|and also|now make|"
                     r"can you (also|make|change)|let'?s (make|change)|switch (it|that)|move (it|that)|turn (it|that) into|"
                     r"(and )?then)\b", re.I)
_QUESTION = re.compile(r"^\W*(are you sure|really|did you|have you|is it|was it|is that|did it|so is it)\b", re.I)


def read_reaction(prev: dict, said: str, ask=_ask) -> tuple[str, str]:
    text = (said or "").strip()
    if not _WRONG.search(text):
        if _CHANGE.search(text):
            return "change", ""
        if _QUESTION.search(text):
            return "continue", ""
    out = ask("You read how a user reacted to what an assistant just did. thanks: pleased or accepting. "
              "correction: says it was wrong, incomplete, not what they asked, or that it already did/didn't do something. "
              "If the user says what was wrong with the result (too long, wrong one, not that) it is a correction even when they also ask for a redo. "
              "change: they change, add to or refine what they want now ('make it every day', 'actually 7pm', 'also add X') "
              "without saying the result failed what they asked: NOT a correction. A question ('are you sure?', 'did you do it?') is continue. "
              "retry: asks again with no complaint about the result. continue: moves on to a related next step. "
              "unrelated: a new topic. In issue, say in under 12 words what was wrong (empty if nothing).",
              f"The assistant did: {prev['tool']} for the request \"{prev['asked']}\". It reported: {prev['result'][:240]}\n"
              f"The user then said: {said[:300]}", REACTION_SCHEMA, 200)
    if not out or out.get("reaction") not in REACTIONS:
        return "unrelated", ""
    reaction, issue = out["reaction"], str(out.get("issue", ""))[:200]
    if reaction == "retry" and len(issue.split()) >= 2:      # a redo that names what was wrong is a correction
        reaction = "correction"
    return reaction, issue


# ── 5. credit, only to the notes that were in the prompt ─────────────────────
def credit(paths: list, verdict: str, strength: str = "verified") -> None:
    """verified pass / fail move a note fully; a judged verdict is not applied here (it only writes lessons)."""
    if not paths or verdict not in ("pass", "fail") or strength != "verified":
        return
    try:
        from brain import brain_store
        brain_store.note_used(paths, True if verdict == "pass" else False)
    except Exception as exc:
        log.warn(f"[Outcome] could not credit notes: {exc}")


# ── 6. lessons written for the model ────────────────────────────────────────
def _slug(text: str, n: int = 48) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", text)).strip()[:n].strip() or "lesson"


def _format(l: dict, evidence: str) -> str:
    return (f"WHEN: {l['situation'].strip()}\nDO: {l['do'].strip()}\nDON'T: {l['dont'].strip()}\n"
            f"BECAUSE: {l['because'].strip()}\nEVIDENCE: {evidence}")


def _existing_lesson(vault, tool: str, situation: str):
    try:
        hits = vault.retrieve(f"{tool} {situation}", token_budget=400, max_notes=4, min_match=0.3)
    except Exception:
        return None
    for h in hits:
        meta, body, _ = vault.read(h["path"])
        if h["kind"] == LESSON_KIND and meta and meta.get("audience") == "model" and meta.get("tool") == tool:
            return h["path"], meta, body
    return None


def write_lesson(rec: dict, why: str, source: str, ask=_ask, vault=None) -> Path | None:
    """Create or revise the lesson for this situation. `why` is the first failed check or the user's complaint."""
    from brain import brain_store
    vault = vault or brain_store.get_vault()
    if vault is None:
        return None
    old = _existing_lesson(vault, rec["tool"], rec["asked"])
    old_text = ""
    if old:
        old_text = re.sub(r"<!--.*?-->", "", old[2], flags=re.S).strip()
    out = ask("You write a short lesson for an AI assistant that controls a PC, to read before acting next time. "
              "Be concrete and imperative; every field is ONE short sentence of at most 20 words. situation: when this applies, in one line. do: what to do instead. "
              "dont: what not to do. because: the cause, in one line. If an earlier lesson is given, REVISE it "
              "with the new evidence instead of repeating it; keep what is still right. Use only the evidence given: never conclude that an app or tool cannot do the task "
              "unless the evidence says so. If the same error repeated across tries that used different scripts, suspect the checking code, not the script. If the cause is not shown, say so in because and make do: read the real error and change the approach.",
              f"Request: {rec['asked']}\nAction: {rec['tool']} {json.dumps(rec.get('args') or {}, ensure_ascii=False)[:200]}\n"
              f"What happened: {rec['result'][:300]}\nWhat went wrong ({source}): {why[:240]}\n"
              + (f"Earlier lesson:\n{old_text[:600]}" if old_text else ""), LESSON_SCHEMA, 420)
    if not out or not all(str(out.get(k, "")).strip() for k in ("situation", "do", "dont", "because")):
        log.warn(f"[Outcome] no lesson written for {rec['tool']}: the model gave no usable answer")
        return None
    fails = 1 + (int(old[1].get("lesson_fails", 0) or 0) if old else 0)
    evidence = f"{fails} time{'s' if fails != 1 else ''} it went wrong (last: {source}, {datetime.now():%Y-%m-%d})"
    title = old[0].stem if old else f"{rec['tool']} - {_slug(out['situation'])}"
    path = vault.upsert(LESSON_KIND, title, _format(out, evidence), audience="model", tool=rec["tool"], source="mbuddy",
                        lesson_fails=fails, tags=[rec["tool"], "lesson"], last_source=source)
    vault._update_meta(path, lambda m: {**m, "lesson_fails": fails, "last_source": source, "audience": "model"})   # upsert keeps existing scalars
    log.info(f"[Outcome] lesson {'revised' if old else 'written'}: {title}")
    return path


def lessons_for(query: str, tool: str = "", limit: int = 3) -> str:
    """The model's own lessons that match this request, as text for a prompt ("" if none)."""
    try:
        from brain import brain_store
        vault = brain_store.get_vault()
        out = []
        for h in vault.retrieve(query, token_budget=400, max_notes=6, min_match=0.3):
            if h["kind"] != LESSON_KIND:
                continue
            meta, body, _ = vault.read(h["path"])
            if not meta or meta.get("audience") != "model" or (tool and meta.get("tool") != tool):
                continue
            out.append("- " + re.sub(r"\s+", " ", re.sub(r"<!--.*?-->|EVIDENCE:.*", "", body, flags=re.S)).strip())
        return "\n\nLessons from your own earlier mistakes (follow them):\n" + "\n".join(out[:limit]) if out else ""
    except Exception:
        return ""


# ── the turn hooks ────────────────────────────────────────────────────────────
def _bg(fn, *args) -> None:
    def run():
        try:
            from core.status import runtime_status
            for _ in range(60):                       # a turn in progress goes first
                if not getattr(runtime_status, "thinking", False):
                    break
                time.sleep(1)
            fn(*args)
        except Exception as exc:  # noqa: BLE001
            log.warn(f"[Outcome] background step failed: {type(exc).__name__}: {exc}"[:200])
    threading.Thread(target=run, name="mbuddy-outcome", daemon=True).start()


_STOP = {"with", "that", "this", "what", "have", "from", "your", "make", "made", "user", "when", "asked", "about", "into",
         "there", "their", "please", "could", "would", "should", "they", "them", "then", "than", "just", "only", "also",
         "some", "more", "time", "does", "doing", "done", "want", "need", "like", "tell", "show", "give", "open", "file"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower()) if w not in _STOP}


def related_notes(notes: list, tool: str, args: dict, asked: str) -> list:
    """The notes from the prompt that bear on this action: about the same tool or app, or sharing a real word with the
    request. The others were only in the prompt (a Blender rule on a World Cup search, the field test: 53 credits to
    unrelated notes against 3 useful ones) and are neither credited nor blamed."""
    tool_words = {tool.lower(), *tool.lower().split("_")} - {"app", "use", "add", "get", "set"}
    apps = {str(v).lower() for k, v in (args or {}).items() if k in ("app", "name", "location") and v}
    asked_w = _words(asked)
    keep = []
    for n in notes or []:
        title = Path(str(n)).stem.lower()
        title_w = _words(title)
        if any(t and t in title for t in tool_words) or any(a and a.split()[0] in title for a in apps) or (title_w & asked_w):
            keep.append(n)
    return keep


def record_turn(user_text: str, results: list, notes_used: list | None = None, background=None, ask=_ask) -> list[dict]:
    """After the tools ran: one outcome per tool, a verdict by code, credit, and a lesson on a failure."""
    global _last
    background = background or _bg
    out = []
    for name, args, result in results or []:
        if name in ("list_abilities", "pc_status", "search_notes", "music_now_playing"):
            continue                                   # reads: nothing to judge
        verdict, reason = classify(name, result)
        rec = {"id": _new_id(), "t": time.time(), "asked": (user_text or "")[:240], "tool": name,
               "args": args if isinstance(args, dict) else {}, "result": str(result)[:500], "verdict": verdict,
               "strength": "verified" if verdict in ("pass", "fail") else "none", "reason": reason,
               "notes": [str(p) for p in related_notes(notes_used or [], name, args if isinstance(args, dict) else {}, user_text)],
               "reaction": None}
        if verdict == "fail" and rec["notes"]:
            # the lesson first, then the demotion (a failing note is replaced by what to do instead)
            background(_fail_chain, rec, reason, "code check", ask)
        else:
            if verdict == "fail":
                background(_lesson_only, rec, reason, "code check", ask)
            credit(rec["notes"], verdict, rec["strength"])
            if verdict == "executed" and name not in ASYNC_TOOLS:
                background(_judge_later, rec, ask)
        _append(rec)
        out.append(rec)
        if name == "use_app" and verdict in ("executed", "unknown"):
            _watch_jobs()
            with _lock:
                _pending_jobs.append(rec)
        with _lock:
            _last = rec
        log.info(f"[Outcome] {name}: {verdict}" + (f" ({reason[:80]})" if reason else ""))
    return out


# ── background jobs: the real ending of "doing it now" ───────────────────────
ASYNC_TOOLS = {"use_app"}      # the reply only says the work began; the job's ending is the verdict
_pending_jobs: list = []        # use_app turns whose work is still running
_listening = False


def _watch_jobs() -> None:
    global _listening
    if _listening:
        return
    _listening = True
    from core import jobs
    jobs.on_change(on_job)


def _driver_error(app: str, since: float) -> str:
    """The newest real error the app driver wrote down for this app during the job (its Symptom section)."""
    try:
        from brain import brain_store
        vault = brain_store.get_vault()
        folder = vault.base / "Debugging experience"
        files = [f for f in folder.glob("*.md") if f.stat().st_mtime >= since - 5 and app.lower() in (f.stem + f.read_text(encoding="utf-8", errors="ignore")[:600]).lower()]
        if not files:
            return ""
        text = max(files, key=lambda f: f.stat().st_mtime).read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"## Symptom\s*(.*?)\s*(?:## |<!--)", text, re.S)
        lines = [ln.strip() for ln in (m.group(1) if m else "").splitlines() if re.search(r"error|exception|traceback|attribute|not found", ln, re.I)]
        return (lines[-1] if lines else "")[:240]
    except Exception:
        return ""


def on_job(event: str, job, background=None, ask=_ask) -> None:
    """A background app job ended: its checks, not the launch sentence, are the verdict for the turn that started it."""
    if event not in ("done", "failed", "stalled") or getattr(job, "kind", "") != "app":
        return
    with _lock:
        rec = next((r for r in reversed(_pending_jobs) if str((r.get("args") or {}).get("app", "")).lower() == str(job.subject).lower()), None)
        if rec is None:
            return
        _pending_jobs.remove(rec)
    verdict = "pass" if event == "done" and job.ok else "fail"
    why = " | ".join(x for x in (getattr(job, "last", ""), getattr(job, "outcome", "")) if x)[:200] or "the job did not finish"
    seen = _driver_error(job.subject, getattr(job, "started", 0.0))
    if seen:
        why = f"{why} | the real error: {seen}"
    _append({"id": rec["id"], "job": verdict, "job_outcome": why, "strength": "verified", "t": time.time()})
    rec = {**rec, "result": f"{job.subject} job {'finished' if verdict == 'pass' else 'failed'}: {why}"}
    log.info(f"[Outcome] job for {job.subject}: {verdict} ({why[:80]})")
    with _lock:
        global _last
        _last = rec
    if verdict == "fail":
        (background or _bg)(_fail_chain, rec, why, "app job", ask)
    else:
        credit(rec["notes"], "pass", "verified")


def _lesson_only(rec, why, source, ask):
    write_lesson(rec, why, source, ask=ask)


def _fail_chain(rec, why, source, ask):
    write_lesson(rec, why, source, ask=ask)
    credit(rec["notes"], "fail", "verified")    # a failure seen by code is evidence on its own


def _judge_later(rec, ask):
    global _last_judge
    if time.time() - _last_judge < JUDGE_GAP_S:
        return
    _last_judge = time.time()
    verdict, reason = judge(rec, ask=ask)
    rec["judged"], rec["judge_reason"] = verdict, reason
    _append({"id": rec["id"], "judged": verdict, "judge_reason": reason, "t": time.time()})
    if verdict == "fail":
        write_lesson(rec, reason or "the report did not show the request was met", "self check", ask=ask)
    log.info(f"[Outcome] self-check of {rec['tool']}: {verdict}")


def on_user_message(text: str, background=None, ask=_ask) -> None:
    """The user's next message is the strongest signal about the previous outcome."""
    global _last
    background = background or _bg
    with _lock:
        prev, _last = _last, None
    if not prev or time.time() - prev["t"] > REACTION_WINDOW_S or not (text or "").strip():
        return
    background(_react, prev, text, ask)


def _react(prev, text, ask):
    reaction, issue = read_reaction(prev, text, ask=ask)
    _append({"id": prev["id"], "reaction": reaction, "issue": issue, "t": time.time()})
    log.info(f"[Outcome] the user's reaction to {prev['tool']}: {reaction}")
    if reaction == "correction":
        lesson = write_lesson(prev, issue or f"the user said: {text[:160]}", "user correction", ask=ask)
        # lesson first, then the demotion: a note is only lowered when what to do instead was written down
        # (the field test: 20 corrections, no lesson, and unrelated notes halved)
        if lesson is not None:
            credit(prev["notes"], "fail", "verified")        # the user's word outranks any check
        else:
            log.info(f"[Outcome] correction of {prev['tool']} kept no demotion: no lesson could be written")
    elif reaction == "thanks" and prev["verdict"] in ("pass", "executed"):
        credit(prev["notes"], "pass", "verified")


_last_id = [0]


def _new_id() -> str:
    """One id per outcome, even for two in the same millisecond (they used to collide and one replaced the other)."""
    n = max(int(time.time() * 1000), _last_id[0] + 1)
    _last_id[0] = n
    return f"o{n}"


def summary(days: int = 7) -> dict:
    """For the What I learned screen: counts of real outcomes."""
    cutoff = time.time() - days * 86400
    base: dict[str, dict] = {}
    for r in recent(2000):
        if "asked" in r:
            base[r["id"]] = dict(r)
        elif r.get("id") in base:
            base[r["id"]].update({k: v for k, v in r.items() if k not in ("t", "id")})
    rows = [r for r in base.values() if r["t"] >= cutoff]
    return {"turns": len(rows), "passed": sum(r["verdict"] == "pass" or r.get("job") == "pass" for r in rows),
            "failed": sum(r["verdict"] == "fail" or r.get("judged") == "fail" or r.get("job") == "fail" for r in rows),
            "corrected": sum(r.get("reaction") == "correction" for r in rows),
            "thanked": sum(r.get("reaction") == "thanks" for r in rows)}
