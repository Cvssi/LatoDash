# LatoDash

A web dashboard for a LatoChan Discord bot server, opened in a browser on your own PC. It manages the server over **one SSH connection**: live logs, token/latency stats, GPU/CPU telemetry, live bot settings, GIF library, persona and memory editors, and a chat CLI to the model. No new ports are opened on the server.

## Requirements
- A server already running **LatoChan** (it provides the `lato-config`, `lato-memory`, `lato-gifs`, `lato-say` commands this dashboard calls) with Ollama.
- SSH access to that server from your PC, Python 3.11+ on the PC.

## Quick start
```bash
git clone https://github.com/Cvssi/LatoDash.git latodash
cd latodash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m latodash --demo --port 8766        # try it with simulated data, no server needed
```
Windows: double-click `LatoDash demo.bat` (demo) or `LatoDash.bat` (real). They create a private `.venv` on first run.

Repository owner: `Cvssi`. Full setup and server account instructions: [GETTING_STARTED.md](GETTING_STARTED.md). GitHub release checks: [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md).

### Connect it to your server
1. `cp config.example.toml config.toml` and fill in `[ssh] host` and `user`. There is no default SSH username; set the account created for your installation. Prefer a dedicated dashboard account without general sudo access.
2. Create a dedicated key: `ssh-keygen -t ed25519 -f keys/latodash_ed25519`, add the `.pub` line to the server user's `~/.ssh/authorized_keys` (ideally prefixed with `from="<your subnet>"`).
3. Pin the server's host key after verifying its fingerprint: `ssh-keyscan <host> > keys/known_hosts`.
4. On the server, install the helper and the narrow sudo rules (replace `YOUR_DASHBOARD_USER` in the sudoers template first):
   ```bash
   sudo install -o root -g root -m 755 server/lato-persona-install /usr/local/sbin/
   sudo install -o root -g root -m 755 server/lato-set-model /usr/local/sbin/
   sudo visudo -cf server/sudoers-latodash && sudo install -m 440 server/sudoers-latodash /etc/sudoers.d/latodash
   ```
5. Add your SSH user to the `latochan` group (`sudo usermod -aG latochan <user>`) so it can edit settings and GIFs without sudo.
6. Run `python -m latodash` (or `LatoDash.bat`) and open <http://127.0.0.1:8765>.

`keys/` and `config.toml` are git-ignored. Never commit them.

## What's in it

| Tab | What it shows / does |
|---|---|
| **Overview** | LatoChan and model status, generation speed, 24 h token totals, replies, live inference progress (prompt %, context fill, last message and reply), health alerts, CPU/GPU/RAM/disk/network with 5-minute charts, token speed per request (24 h), tokens per hour, recent API calls, warnings and services |
| **Logs** | Every journal line from latochan, ollama, GPU services and the kernel. You can filter by source and level, search (`/` focuses the search box), pause or follow, and export |
| **API calls** | Every Ollama HTTP request (status and latency), and each Discord reply. There is also a per-request LLM table with prompt and output tokens, prompt and generation speed, total time and context used |
| **Settings** (1.0.1) | Every live knob for her, grouped: thinking mode, creativity/top-p/reply length/conversation memory, **sleep and wake times**, check-ins and quiet hours, **gif odds** (post chance, waking-gif chance, cooldown), chat timing, abilities (pictures, notes, server actions) and **sass/swearing** dials. Sliders and switches are built from the schema the server reports, rows grey out when their master switch is off, and a bar at the bottom saves or discards. Changes reach her on her next message, no restart |
| **GIFs** (1.0.1) | The library behind `[[GIF: mood]]`: paste links, load a `.txt`/`.json` file, import from (or copy to) a local folder (`app.latochan_dir`), remove single links, clear a mood.  |
| **Tools** | Admin actions as buttons: **Restart LatoChan**, **Restart Ollama**, a **Thinking mode** switch, **Pull/Push persona** and **Pull/Push memory** (in-browser editors; Ctrl+S pushes), **List notes**, **Post as LatoChan**, and **Load/Unload models** |
| **Brain CLI** | The live brain view (IN / RAW / THINK / replied, same colours). Type to talk to the model directly, with tokens streamed and speed shown after each answer. `/help` lists the commands |

### Brain CLI commands
`/help` `/clear` `/reset` (new conversation) `/model <name>` `/models` `/persona on|off` `/think on|off` `/temp <0-2>`
`/ollama on|off` (show runner lines) `/say <text>` (post to Discord) `/mem [name]` `/thinking on|off|toggle` (bot setting)
`/restart` `/status` `/history` `/stop`. Esc stops an answer, ↑/↓ go through history, and Tab completes commands.

Set the dashboard `num_ctx` to match the bot. Model residency also depends on the configured keep-alive values and available memory.
The model handles one request at a time. A CLI question asked while LatoChan is answering waits its turn.

### Alerts it raises
- The server is unreachable, or telemetry has stopped.
- `latochan` or `ollama` is not active, or systemd auto-restarted one of them.
- A GPU service has failed.
- The Ollama API is not responding or is slow.
- The model is not loaded (her next reply will need a cold load).
- **Possible hang:** a message has waited more than 120 s for the model (critical after 300 s), or one generation has run longer than 180 s.
- The GPU hotspot is at 95 °C or above (critical at 100 °C).
- VRAM is nearly full, the disk is above 90 %, or RAM is low.
- Error lines appeared in the last 15 minutes.
- Kernel errors (amdgpu) appeared in the last 24 hours.

You can change the thresholds in `config.toml` under `[thresholds]`.

## How it works

```
 browser ──http/ws──▶ LatoDash (this PC, 127.0.0.1:8765) ══ one SSH connection ══▶ server
                                                             ├─ collector.py via stdin (nothing installed; stdlib only)
                                                             ├─ journalctl -f -o json  (latochan, ollama, gpu-*, timers, kernel)
                                                             ├─ fixed commands for tools (lato-config, lato-memory, lato-say, systemctl)
                                                             └─ port-forward to Ollama 127.0.0.1:11434 (chat, load/unload)
```

- **No new ports are opened on the server.** Everything uses the existing SSH (LAN-only, firewalled).
- **Token stats** come from llama.cpp's own timing lines in the Ollama journal, so they cover every request the bot sends.
  On start, the last 24 h of history is loaded from the journal.
- **Security:** the dashboard only listens on 127.0.0.1. The page also guards against other sites you have open:
  - the Host header must be a loopback address (stops DNS rebinding),
  - the Origin header must be the dashboard's own,
  - state-changing calls must carry an `X-LatoDash` header,
  - a strict Content-Security-Policy is set.

  Text you type (persona, memory, Discord messages) goes to the server on **stdin only**, never into a command line.
  Inputs are validated: model names, channel IDs and the thinking mode.
- **SSH key:** `keys/latodash_ed25519` is the dashboard's own key (never shared, git-ignored). Restrict it on the server with `from="<your subnet>"` and revoke it by deleting its line from `~/.ssh/authorized_keys`. The server's host key is pinned in `keys/known_hosts`.
- **Local copies:** Pull persona and Pull memory refresh `persona.md` and `latochan-memory.txt` in `app.latochan_dir` (if set) (the previous copy is kept as `.pc-backup`).
  Push persona makes a timestamped `.bak-…` on the server.
- Dashboard logs are written to `logs\latodash.log` (rotated).

## sudo
Restart, Push persona and Post as LatoChan use `sudo -n` (no password prompt), so they need passwordless sudo for exactly those commands.
The narrow rules live in `/etc/sudoers.d/latodash` (source: `server/sudoers-latodash`). They allow only:
- `systemctl restart latochan.service`
- `systemctl restart ollama.service`
- `lato-say`
- `/usr/local/sbin/lato-persona-install` (source: `server/lato-persona-install`). It reads the new persona from stdin, backs up the old one, and installs the new one.

The server runs sudo-rs, which rejects wildcards in sudoers arguments. That is why the persona upload goes through a fixed helper script.
Because of these rules you never need a broad `NOPASSWD:ALL` sudo rule for the dashboard.

## Development
```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest          # 36 tests: parsers, stats/alerts, security guard, tool safety, config
.venv\Scripts\python -m ruff check latodash tests
.venv\Scripts\python -m latodash --demo --port 8766
```
Code layout: `latodash/` contains:
- `sshlink.py` (connection and reconnect)
- `remote/collector.py` (server telemetry)
- `journal.py` (log parsing)
- `monitor.py` (state, stats, alerts)
- `workers.py`
- `tools.py` (admin actions and CLI chat)
- `app.py` (API)
- `security.py`
- `demo.py`
- `static/`, a dependency-free ES-module frontend: `js/views/*` holds one file per tab, plus `charts.js`, `store.js` and `api.js`.

## Troubleshooting
- **"Server unreachable"**: the server is off or not on the LAN, or the key isn't in `authorized_keys`. The sidebar shows the exact reason.
- **Host key error**: the server was reinstalled. Refresh `keys/known_hosts` (`ssh-keyscan <server> > keys/known_hosts`), but only if you know why the key changed.
- **Port 8765 busy**: if LatoDash is already running, the launcher just opens it. Otherwise change `listen_port` in `config.toml`.


## Compatibility with the reviewed LatoChan 1.1.0 release

Deploy this dashboard together with the reviewed bot and the updated root-owned `server/lato-persona-install` and `server/lato-set-model` helpers. Read `GETTING_STARTED.md` and the bot deployment/recovery guide first. Do not test on production by overwriting live configuration.

- Settings, GIFs and memory already use `lato-config`, `lato-gifs` and `lato-memory`; those stable command names remain compatible. The remote collector only reads the settings snapshot. Stop dashboard writes during installation, then reconnect SSH so new administrative group membership takes effect.
- Persona edits use helper protocol 2 to discover the active protected path from the root-owned environment. The client refuses to upload to an old helper. New bot installations use `/etc/latochan/persona.md`; a protected legacy path is also supported. The helper keeps private unique backups and installs administrator-owned persona files atomically.
- Memory push requires a fresh `#snapshot` export. A stale push returns a conflict and preserves both server notes and local edits. Pull and merge rather than retrying an old export. Automatic private context remains disabled as requested; administrative export/import still exists.
- Partial GIF storage failures are surfaced as conflicts with a refresh instruction. Validation failures still return 422.
- Post-as-bot text uses `sudo -n lato-say --stdin`, keeping content out of process arguments. It requires the updated bot helper. No new sudo rule is required.
- The demo uses bundled rules rather than executing Python from `app.latochan_dir`. Its portable lock implementation supports the Windows demo; real server writes always use the Linux helpers over SSH.
- With bot content logging disabled, the dashboard's historic IN/RAW/THINK panels will not receive new private content. This is intentional. Existing journal history is subject to the operator's retention policy.

After staging and reviewing both releases, install the updated server helpers as administrator:

```bash
sudo install -o root -g root -m 755 server/lato-persona-install /usr/local/sbin/lato-persona-install
sudo install -o root -g root -m 755 server/lato-set-model /usr/local/sbin/lato-set-model
```

The existing narrow sudo command paths remain unchanged. Review the effective sudoers policy with `visudo`; do not add `NOPASSWD:ALL`. Confirm persona pull/push, model switching and rollback, stale memory handling, and permissions using a disposable setup before production.
