# Google Classroom MCP

An [MCP](https://modelcontextprotocol.io) server that gives AI tools (SlideMate, Claude Desktop, Claude Code, Codex)
read-only access to a student's **Google Classroom**: courses, upcoming assignments, announcements and class
materials. It can also **download class slides** (Google Slides and Docs are saved as PDF) into one folder per course.

It's a single Python file with no dependencies, and runs on macOS, Linux or Windows with Python 3.9+.

## Tools

| Tool | What it does |
| --- | --- |
| `gc_login` | Sign in with Google (opens the browser). Needed once; the sign-in is remembered. |
| `gc_status` | Whether it's set up and signed in, and where downloads go |
| `gc_courses` | Active courses |
| `gc_upcoming` | Assignments due in the next N days (default 14) across all courses, with submission status |
| `gc_coursework` | Every assignment in one course: due date, status, description, attachments |
| `gc_announcements` | Recent stream posts in one course |
| `gc_materials` | Class materials in one course, grouped by topic |
| `gc_download` | Download one Drive file (Slides and Docs saved as PDF) |
| `gc_sync` / `gc_sync_status` | Download all new slides and handouts to `~/Classroom/<course>/<topic>/` (runs in the background) |

Courses can be named loosely: `"bio"` finds *Year 12 Biology*.

## Setup (about 10 minutes, once)

Google requires every app that reads Classroom to have its own OAuth client. You create a free one for yourself:

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project, e.g. *Classroom MCP*.
   Any Google account works (a personal Gmail is fine), and it doesn't have to be the school account.
2. **Enable the APIs:** APIs & Services → Library → enable **Google Classroom API** and **Google Drive API**.
3. **Consent screen:** Google Auth Platform → *Get started*. Name it anything. For audience choose **External**.
   Under **Audience → Test users**, add the school email address you'll sign in with.
4. **Create the client:** Google Auth Platform → Clients → *Create client* → type **Desktop app** → Create →
   **Download JSON**.
5. Save the downloaded file as `~/.config/classroom-mcp/client_secret.json`:

   ```bash
   mkdir -p ~/.config/classroom-mcp && mv ~/Downloads/client_secret_*.json ~/.config/classroom-mcp/client_secret.json
   ```

6. Sign in once with `python3 classroom_mcp.py login`, or let your AI tool call `gc_login`. A browser opens. Pick the
   school account and allow access. Google warns that the app is unverified, because it's yours: click
   **Continue**.

**Stay signed in:** while the app is in *Testing*, Google signs you out every 7 days. To avoid that, go to Google
Auth Platform → Audience → **Publish app**. It stays private to you; you'll just see the "unverified app" screen
once more when signing in.

**School accounts:** some schools' Google Workspace admins block apps they haven't approved. If sign-in says
*"Access blocked: … is not approved by your admin"* or *"admin_policy_enforced"*, only the school's IT can allow it.
There's no way around that from your side.

## Use it

### SlideMate

Settings → **Connections & sync** → *Add server*:

- **Name:** `google-classroom`
- **Command:** `python3`
- **Arguments:** `/path/to/classroom_mcp.py`

SlideMate notices `gc_sync`, so a **Pull from Google Classroom** button appears in the library, and `gc_login` is
used when the sign-in has expired. Then add `~/Classroom` as a library folder (Settings → Library → *Add folder…*):
each course becomes a course in SlideMate, and the tutor can check deadlines with `gc_upcoming`.

### Claude Code

```bash
claude mcp add google-classroom -- python3 /path/to/classroom_mcp.py
```

### Claude Desktop / other MCP clients

```json
{
  "mcpServers": {
    "google-classroom": { "command": "python3", "args": ["/path/to/classroom_mcp.py"] }
  }
}
```

## Settings (environment variables, all optional)

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLASSROOM_DIR` | `~/Classroom` | Where `gc_sync` saves files |
| `GOOGLE_CLIENT_SECRETS` | `~/.config/classroom-mcp/client_secret.json` | Your OAuth client file |
| `CLASSROOM_MCP_HOME` | `~/.config/classroom-mcp` | Where the sign-in token and sync state are kept |

PowerPoint and Word files are converted to PDF if [LibreOffice](https://www.libreoffice.org) is installed, and are
otherwise saved as-is.

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

MIT licensed.
