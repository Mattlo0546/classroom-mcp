# Google Classroom MCP Server for Claude, ChatGPT Codex and Cursor

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)](https://modelcontextprotocol.io)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
[![M8ven Score](https://m8ven.ai/badge/mcp/mattlo0546-classroom-mcp-bumwh3?v=ac6c86508de2443d2227e91e4b98dd26)](https://m8ven.ai/mcp/mattlo0546-classroom-mcp-bumwh3?s=readme)

**classroom-mcp** is an open-source [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server for
**Google Classroom**. It gives AI assistants (Claude Desktop, Claude Code, ChatGPT Codex, Cursor, VS Code and
[SlideMate](https://github.com/Mattlo0546/slidemate)) read-only access to a student's Classroom: courses, upcoming
assignments, announcements and class materials. It can also **download class slides**, saving Google Slides and
Docs as PDF, into one folder per course.

- **A single Python file with no dependencies.** It runs on macOS, Linux or Windows with Python 3.9+.
- **Read-only.** It uses read-only Google scopes, so it can't submit work or post anything.
- **Yours.** You sign in with your own Google OAuth client, and nothing goes through a third-party server.

> Using Canvas, Moodle or Blackboard? See [canvas-mcp](https://github.com/Mattlo0546/canvas-mcp),
> [moodle-mcp](https://github.com/Mattlo0546/moodle-mcp) and [blackboard-mcp](https://github.com/Mattlo0546/blackboard-mcp).

## What you can ask

- *"What Classroom assignments are due in the next two weeks, and which have I turned in?"*
- *"Any new announcements in Biology?"*
- *"Download this week's slides from every class."*

## Tools

| Tool | What it does |
| --- | --- |
| `gc_login` | Sign in with Google (opens the browser). You only need to do this once; the sign-in is remembered. |
| `gc_status` | Whether it's set up and signed in, and where downloads go |
| `gc_courses` | Active courses |
| `gc_upcoming` | Assignments due in the next N days (default 14) across all courses, with submission status |
| `gc_coursework` | Every assignment in one course: due date, status, description, attachments |
| `gc_announcements` | Recent stream posts in one course |
| `gc_materials` | Class materials in one course, grouped by topic |
| `gc_download` | Download one Drive file (Slides and Docs are saved as PDF) |
| `gc_sync` / `gc_sync_status` | Download all new slides and handouts to `~/Classroom/<course>/<topic>/` (runs in the background) |

Courses can be named loosely: `"bio"` finds *Year 12 Biology*.

## Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && ~/.local/bin/uv tool install --managed-python https://github.com/Mattlo0546/classroom-mcp/archive/refs/heads/main.zip
```

That installs a `classroom-mcp` command (`~/.local/bin/classroom-mcp`). Or skip installing and run the file
directly: `python3 classroom_mcp.py`.

## Google setup (about 10 minutes, once)

Google requires every app that reads Classroom to have its own OAuth client. You create a free one for yourself:

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project, e.g. *Classroom MCP*.
   Any Google account works (a personal Gmail is fine), and it doesn't have to be the school account.
2. **Enable the APIs:** APIs & Services → Library → enable **Google Classroom API** and **Google Drive API**.
3. **Consent screen:** Google Auth Platform → *Get started*. Name it anything. For audience choose **External**.
   Under **Audience → Test users**, add the school email address you'll sign in with.
4. **Create the client:** Google Auth Platform → Clients → *Create client* → type **Desktop app** → Create →
   **Download JSON**.
5. Save the downloaded file as `~/.config/classroom-mcp/client_secret.json`. In SlideMate, press **Choose
   client file…** on the Google Classroom card instead.

   ```bash
   mkdir -p ~/.config/classroom-mcp && mv ~/Downloads/client_secret_*.json ~/.config/classroom-mcp/client_secret.json
   ```

6. Sign in once with `classroom-mcp login`, or let your AI tool call `gc_login`. A browser opens. Pick the
   school account and allow access. Google warns that the app is unverified, because it's yours: click
   **Continue**.

**Stay signed in:** while the app is in *Testing*, Google signs you out every 7 days. To avoid that, go to Google
Auth Platform → Audience → **Publish app**. It stays private to you; you'll just see the "unverified app" screen
once more when signing in.

**School accounts:** some schools' Google Workspace admins block apps they haven't approved. If sign-in says
*"Access blocked: … is not approved by your admin"* or *"admin_policy_enforced"*, only the school's IT can allow it.
There's no way around that from your side.

## Connect it to your AI app

### SlideMate

In [SlideMate](https://github.com/Mattlo0546/slidemate), go to Settings → **Your university** → **Google
Classroom**. Choose your client file, then press **Connect**. SlideMate installs this server, signs you in and
makes the library's **Pull** button download new class slides into your library.

### Claude Code

```bash
claude mcp add google-classroom -- ~/.local/bin/classroom-mcp
```

### Claude Desktop, Cursor, VS Code

```json
{
  "mcpServers": {
    "google-classroom": { "command": "/Users/YOU/.local/bin/classroom-mcp" }
  }
}
```

(VS Code's `.vscode/mcp.json` uses `"servers"` in place of `"mcpServers"`.)

### ChatGPT Codex CLI

```bash
codex mcp add google-classroom -- ~/.local/bin/classroom-mcp
```

## Settings (environment variables, all optional)

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLASSROOM_DIR` | `~/Classroom` | Where `gc_sync` saves files |
| `GOOGLE_CLIENT_SECRETS` | `~/.config/classroom-mcp/client_secret.json` | Your OAuth client file |
| `CLASSROOM_MCP_HOME` | `~/.config/classroom-mcp` | Where the sign-in token and sync state are kept |

PowerPoint and Word files are converted to PDF if [LibreOffice](https://www.libreoffice.org) is installed, and are
otherwise saved as-is.

## FAQ

**Can an AI assistant read my Google Classroom?**
Yes. Install this MCP server, connect it to Claude, Codex, Cursor or SlideMate as shown above, and ask about your
assignments, announcements or materials.

**Why do I need my own Google Cloud project?**
Google only lets apps read Classroom through a registered OAuth client. Using your own keeps the access private to
you, with no shared app and no server in the middle.

**Can it submit assignments?**
No. It only asks for read-only scopes.

## Privacy

- **Access:** read-only. It can't submit work, post or change anything in Classroom or Drive.
- **Storage:** the sign-in token stays on your computer (`~/.config/classroom-mcp/token.json`, readable only by
  you). Data goes directly between your computer and Google.
- **Scopes:** `classroom.courses.readonly`, `classroom.coursework.me.readonly`,
  `classroom.courseworkmaterials.readonly`, `classroom.announcements.readonly`, `classroom.topics.readonly` and
  `drive.readonly`. Drive access is needed because class files live in the teacher's Drive.

## Development

```bash
python3 tests/test_classroom.py   # offline tests: MCP protocol, sign-in flow, sync (fake Google responses)
```

## Related

- [canvas-mcp](https://github.com/Mattlo0546/canvas-mcp): MCP server for Canvas LMS (Oxford, Harvard, Stanford…)
- [moodle-mcp](https://github.com/Mattlo0546/moodle-mcp): MCP server for Moodle (UCL, King's, LSE, Cambridge…)
- [blackboard-mcp](https://github.com/Mattlo0546/blackboard-mcp): MCP server for Blackboard Learn (Imperial, Edinburgh…)
- [SlideMate](https://github.com/Mattlo0546/slidemate): a free Mac app with an AI tutor for your lecture slides. It
  sets these up in one click.

MIT licensed. Not affiliated with Google. Google Classroom is a trademark of Google LLC.
