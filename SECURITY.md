# Security

## Reporting a vulnerability

Please report it privately rather than in a public issue: use GitHub's "Report a vulnerability" (Security tab) on this repository.
Include what you found, how to reproduce it, and what it could let someone do. You will get an answer, and a fix before any
public write-up.

## What the phone app protects, and how

| Thing | How it is handled |
|---|---|
| The cloud key and the PC pairing token | Stored encrypted with a key held in Android's secure hardware; never shown to the page, never exported, never written to a log. |
| What the buddy hears and says | Log lines in release builds show only lengths, never the words. |
| What the page can ask the app to do | Every bridge method checks that the call came from the bundled page, not from a web page opened elsewhere. |
| Acting in other apps | Needs the owner's Android accessibility switch; refuses banking, payment, password and security apps; asks for a yes before sending, paying, deleting or calling; never types into password fields. |
| Backups | Android backup is off; the owner's own export file never contains keys or tokens. |

## Known limits (stated honestly)

- Screen text is read by a language model that can tap. The guards above reduce the risk of hostile text on a page steering it, but
  they do not remove it. Treat screen control as something you turn on for tasks you give it.
- Plain HTTP is allowed for the PC pairing on private addresses only (checked in code), because Android cannot express "private
  addresses only" in its network configuration.
- A release is currently signed with a debug key until a proper release key is set up (see `docs/BUILD.md`).
