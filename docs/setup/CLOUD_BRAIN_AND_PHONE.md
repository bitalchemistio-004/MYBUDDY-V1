# Where MBUDDY thinks, your Ollama key, usage, and the phone

For new users. Everything here is also in the app: the first-run setup has the steps, and Settings, Models and
Settings, Usage have the controls. Written 2026-10-08.

## 1. Choose where I think
| | On this PC | In the cloud |
|---|---|---|
| Model | a small local model (qwen3 4B) through Ollama | a cloud model on Ollama's servers (default `gemma4:31b`) |
| Good for | privacy, offline, no account | weak laptops, no graphics card, keeping the PC light |
| Needs | about 4 GB free memory, a one-time 2.5 GB download | internet and your own Ollama key |
| What leaves the PC | nothing | what you say to me, and the notes I look up to answer |
| Tools, notes, lessons | all of them | all of them (same pipeline; only the model changes) |

Switching is **at once**. Going to the cloud frees the model on this PC (and ends the Ollama server MBUDDY started;
one you run yourself is only unloaded). Going back starts it again, which takes a few seconds. Hearing and voice have
their own choice, "the graphics card / the processor only", which applies after a restart.

**Why `gemma4:31b` is the default cloud brain.** In my own 32-task test (tool choice, strict formats, reading
long text, reasoning, pictures) it passed 31, answered in about a second, sees pictures, and has the lowest list
price of the reliable models (about $0.14 per million tokens in, $0.40 out on a paid plan). `gpt-oss:120b`
is the strong alternative. Pick another in Settings, Models; "Check my plan" tells you which ones your plan allows.


## 2. Your Ollama key
1. Make a free account at ollama.com, then create a key at **ollama.com/settings/keys**.
2. In MBUDDY: Settings, Models, "Where I think", paste the key and press **Check and save**.
3. MBUDDY tries it with one tiny request first. A wrong key is refused and nothing is saved.

How it is kept: encrypted for your Windows account (Windows DPAPI) in `%USERPROFILE%\.buddy\ollama_cloud_key.secret`.
Another Windows user, or the file copied to another PC, cannot read it. MBUDDY never shows it again, never writes it
to a log and never sends it anywhere except ollama.com. A key in the `.env` file (`OLLAMA_CLOUD_API_KEY`) still
works; a key saved in the app wins. **Remove** deletes it from this PC. If a key ever leaks, delete it on
ollama.com too.

## 3. Warnings
- **Your words leave this PC** with the cloud brain. Use the PC brain for anything you would not send to a service.
- **Internet is required** for the cloud brain.
- **Free plan limits.** Ollama does not publish the free allowance and allows one request at a time. Busy moments
  can be slower or stop until the limit resets. A paid plan lifts this.
- **A model your plan does not include** answers "not in your plan"; MBUDDY then leaves it alone for ten minutes
  instead of retrying every turn.
- **Anything taken from your PC** (clipboard, a file, the screen) is sent to the bigger model only after you say yes.
  This is not switchable.
- You can set a **daily token limit** in Models; calls stop when it is reached.

## 4. The Usage page
Settings, **Usage** shows, for 7 or 30 days: tokens today and in the period, calls, failures, a bar per day, each
model's tokens and list-price estimate, what each job used (everyday thinking, hard questions, screen questions),
and the latest calls. Notes:
- Only cloud calls are counted. The model on this PC costs nothing.
- The price is an estimate at Ollama's list prices on a paid plan, and only for models with a known price; others
  are named as "no price known". On a free plan you are not billed per token.
- It counts what MBUDDY sent and received. Ollama's own usage page is the source of truth for your plan.

## 5. Use MBUDDY from your phone (optional)
1. Install **Tailscale** (free, tailscale.com/download) on this PC and on your phone. Sign in to the same account.
2. On the PC, once, in a terminal: `tailscale serve --bg 5051`
3. In MBUDDY, open **Away**, press **New pairing code**, open the address shown on the phone and type the code
   (six digits, valid five minutes, usable once).
4. Use the switch at the top, **On my PC / On my phone**, to say where you are working. On the phone setting the PC
   stays quiet and replies, reminders and questions that need your yes go to the phone.

Warnings:
- **Use Tailscale Serve, never Funnel.** Funnel publishes the address on the public internet.
- A paired phone can only do a short list of things (see status, tasks, ask me, answer yes/no, add or finish a task,
  start a focus sprint, run a routine, switch where I work). It cannot change settings, read notes or touch files.
- **Pause access** (Away) cuts every phone off at once. **Remove** forgets one phone. Away lists what each phone did.
- Keep the phone's address private if you use it with Google's AI Edge Gallery: it works like a key for that phone.
- Allowing your home Wi-Fi as well is off by default and not encrypted; use it only on a network you trust.
- Not tested yet on a real phone: pairing over Tailscale, the Gallery, push notifications.

## 6. Things that go wrong
| What you see | Why | What to do |
|---|---|---|
| "Ollama refused this key" | typo, or the key was deleted | copy it again from ollama.com/settings/keys |
| "isn't on this plan" | the model needs a paid plan | choose another model, or use Check my plan |
| "The cloud is busy or the limit was reached" | free plan allowance | wait, or set a smaller daily limit, or upgrade |
| "Restart needed" stays after switching | the choice applies at start | press Restart MBUDDY now |
| Answers stopped after switching to the cloud | no internet, or the key was removed | check the internet; Settings, Models |


## 7. Phone tools (works without the PC)
Open **Phone tools** from the phone's Away screen, or press and hold the MBUDDY icon on the home screen and choose
"Phone tools". The page is kept on the phone, so it opens when the PC is off.
- **Tell me:** type or speak a line. On the phone, with no PC: "10 minute timer", "alarm at 7:30", "remind me to call Sara
  tomorrow at 9", "call +212600111222", "text +212600111222 on my way", "directions to Central Station", "search pasta recipe".
  Anything else goes to MBUDDY on the PC when it can be reached.
- **How it does things:** a web page cannot set an alarm by itself. Android lets a link open the alarm clock, timer,
  calendar, dialer, messages or maps with the details filled in, so every action is **one tap** on a button. Nothing
  happens silently. Calendar events also come as a Google Calendar link and a calendar file (.ics) in case your calendar
  app doesn't take the first.
- **Tasks:** kept on the phone and sent to the PC when it can be reached ("n waiting to reach the PC" is shown).
- **From your PC:** say "set a timer on my phone", "put an alarm on my phone for 7:30" or "remind my phone to ..." to MBUDDY
  on the PC: it appears on the phone as a button to tap.
- **The AI on this phone:** the page reads what it can about the phone (Android, memory) and says which Gemma 4 model to get in
  Google AI Edge Gallery (E4B, 3.65 GB, for 8 GB or more; E2B, 2.58 GB, for 4 to 6 GB; nothing below that), how to install the
  app (one button that opens it or its Play Store page), and gives the address for adding MBUDDY's tools to it.
- **Limits:** a web page cannot read your contacts (type a number), cannot see which apps are installed (the Gallery button
  opens the app or the Play Store page), and cannot click for you. Full control of the phone (accessibility service, reading
  other apps, acting without a tap) needs a native Android app.

**Do I need a USB cable and USB debugging?** No, not for anything above. It is only needed later if we build a native Android app
(to install it on the phone and read its error log while developing). USB debugging lets a computer control the phone, so leave
it off unless a developer step needs it.
