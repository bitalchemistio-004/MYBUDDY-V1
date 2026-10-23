// Learning memory: which Obsidian vault MBUDDY writes its lessons and skills to (api/brain_api.py: /brain/status,
// /brain/link, /brain/unlink). Shown in Settings, General and, when nothing is linked, on Brain, Learned. The Brain page
// used to say "Link one in Settings" while Settings had no such control.
import { api } from "../api.js";
import { esc } from "./ui.js";

export async function mountVaultCard(el, ctx, onChange = () => {}) {
  el.className = "surface card stack";
  let st = null, editing = false, confirmUnlink = false, busy = false;

  const draw = () => {
    if (!st) { el.innerHTML = `<div class="label">Learning memory</div><div class="empty">Checking…</div>`; return; }
    if (!st.ok && st.ok !== undefined) {
      el.innerHTML = `<div class="label">Learning memory</div><div class="empty">I couldn't read this. Check Status, then open this page again.</div>`;
      return;
    }
    const pathRow = `<div class="row" style="gap:var(--s2);flex-wrap:wrap">
        <input class="field" id="vc-path" style="flex:1;min-width:220px" placeholder="C:\\Users\\you\\Documents\\My vault" aria-label="Vault folder">
        <button class="btn small" type="button" data-act="path">Use this folder</button>
        ${st.linked ? `<button class="btn small ghost" type="button" data-act="cancel">Cancel</button>` : ""}</div>`;
    const intro = `<div><div class="label">Learning memory</div>
        <p class="muted" style="margin:4px 0 0">Where I keep what I learn (lessons from mistakes, skills that worked) as notes you can read
        and edit in <b>Obsidian</b>. Your facts and reminders don't need it.</p></div>`;
    if (st.linked) {
      el.innerHTML = `${intro}
        <div class="setting"><div><div class="t">On: ${esc(st.notes)} note${st.notes === 1 ? "" : "s"}</div>
          <div class="s" style="word-break:break-all">${esc(st.folder || st.vault)}</div></div>
          <div class="btnrow">
            <button class="btn small" type="button" data-act="change">Change folder</button>
            <button class="btn small danger" type="button" data-act="unlink">${confirmUnlink ? "Sure? I'll stop learning" : "Unlink"}</button>
          </div></div>
        ${editing ? pathRow : ""}
        ${st.obsidian_installed ? "" : `<p class="faint" style="margin:0">To read the notes, install Obsidian (free, obsidian.md) and open this folder as a vault.</p>`}`;
    } else {
      el.innerHTML = `${intro}
        <div class="empty" style="text-align:left">Off: I'm not learning yet. Choose where my notes go:</div>
        <div class="btnrow" style="flex-wrap:wrap">
          ${st.detected ? `<button class="btn accent" type="button" data-act="detected" title="${esc(st.detected)}">Use my Obsidian vault</button>` : ""}
          <button class="btn ${st.detected ? "" : "accent"}" type="button" data-act="create">Create a new vault</button>
        </div>
        ${st.detected ? `<p class="faint" style="margin:0;word-break:break-all">Your Obsidian vault: ${esc(st.detected)}</p>` : ""}
        <p class="faint" style="margin:0">A new vault is a folder in Documents called "MBUDDY Vault".
          ${st.obsidian_installed ? "" : "Install Obsidian (free, obsidian.md) to read and edit the notes; I can learn without it."}</p>
        <div class="label" style="margin-top:var(--s2)">Or use a folder you already have</div>
        ${pathRow}`;
    }
  };

  const load = async () => {
    st = await api.get("/brain/status").catch(() => ({ ok: false }));
    draw();
  };

  const link = async (body, done) => {
    if (busy) return;
    busy = true;
    const r = await api.post("/brain/link", body).catch(() => ({ ok: false, error: "No answer from MBUDDY." }));
    busy = false;
    if (r.ok === false) { ctx.toast(r.error || "Couldn't link that folder."); return; }
    st = r; editing = false; confirmUnlink = false; draw();
    ctx.toast(done); onChange(st);
  };

  el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const act = b.dataset.act;
    if (act === "detected") return link({ vault: st.detected }, "Linked to your Obsidian vault. I'll keep what I learn there.");
    if (act === "create") return link({ create: true }, "Created \"MBUDDY Vault\" in Documents. I'll keep what I learn there.");
    if (act === "path") {
      const v = el.querySelector("#vc-path").value.trim();
      if (!v) { ctx.toast("Type the folder's full path first."); return; }
      return link({ vault: v }, "Linked. I'll keep what I learn in that folder.");
    }
    if (act === "change") { editing = !editing; draw(); if (editing) el.querySelector("#vc-path")?.focus(); return; }
    if (act === "cancel") { editing = false; draw(); return; }
    if (act === "unlink") {
      if (!confirmUnlink) { confirmUnlink = true; draw(); return; }
      const r = await api.post("/brain/unlink", {}).catch(() => ({ ok: false }));
      if (r.ok === false) { ctx.toast("Couldn't unlink."); return; }
      st = r; confirmUnlink = false; draw();
      ctx.toast("Unlinked. Your notes stay in the folder; I've stopped learning."); onChange(st);
    }
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.id === "vc-path") el.querySelector('[data-act="path"]')?.click();
  });
  draw();
  await load();
}
