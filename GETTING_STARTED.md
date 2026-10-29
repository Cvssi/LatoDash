# Run LatoDash

LatoDash runs on your PC and connects over SSH to a separately installed LatoChan server. It requires Python 3.11+; review testing used Python 3.14. The browser interface listens on localhost.

## Try the demo

Download the published source, or clone `https://github.com/Cvssi/LatoDash.git` after the repository is published.

Windows: double-click `LatoDash demo.bat`. The launcher installs dependencies in `.venv`. For a manual PowerShell setup from the project folder:

```powershell
py -3.14 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m latodash --demo --port 8766
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m latodash --demo --port 8766
```

## Prepare server access

There is no automatic/default SSH username. Set `[ssh].user` to an account that exists on your server. A dedicated account such as `latodash` is recommended. Do not add it to the `sudo`/`wheel` group or grant `NOPASSWD: ALL`. Retain a separate administrator login while testing access.

An administrator can create that account and grant the data/log groups on a systemd server:

```bash
sudo useradd --create-home --shell /bin/bash latodash
sudo usermod -aG latochan,systemd-journal latodash
```

Skip account creation if it already exists. Journal membership permits reading system logs; grant it only to a trusted operator. The `latochan` group permits access to bot settings and private data, so this is a trusted management account.

Create a dedicated Ed25519 key on the PC using `ssh-keygen -t ed25519 -f keys/latodash_ed25519` (create the `keys` directory first). Install only its public key in the dashboard account's `~/.ssh/authorized_keys` using directory mode 0700 and file mode 0600. Restrict its source address where practical. Keep the private key on the PC.

Copy the reviewed `server/` helper files to a staging folder on the server. From the dashboard source folder on that server, install the paired helpers:

```bash
sudo install -o root -g root -m 755 server/lato-persona-install /usr/local/sbin/
sudo install -o root -g root -m 755 server/lato-set-model /usr/local/sbin/
```

Copy `server/sudoers-latodash` to a temporary file and replace `YOUR_DASHBOARD_USER` with the chosen account. Validate it using `sudo visudo -cf /path/to/edited-template`, then install it as `/etc/sudoers.d/latodash` with root:root ownership and mode 0440. Run `sudo visudo -c` and `sudo -l -U latodash` (substitute your account) to confirm only the intended rules apply. Existing broad rules must be reviewed separately; do not remove administrative access blindly.

The dashboard needs SSH local forwarding to reach Ollama at server localhost. Keep Ollama bound to 127.0.0.1; do not expose its port to the LAN or Internet.

## Configure and launch

Copy `config.example.toml` to `config.toml`. Set host, SSH user, dedicated key path and known-hosts path. Set your model, matching context size and optional channel ID. Verify the server SSH host-key fingerprint through a trusted administrator before adding it to `keys/known_hosts`; `ssh-keyscan` alone does not authenticate it.

Reconnect after group changes. On Windows run `LatoDash.bat`; otherwise run `.venv/bin/python -m latodash`. Open http://127.0.0.1:8765. Use the demo or a disposable Discord channel for initial testing; real tool actions can restart services, change stored data or post as the bot.

Do not commit `config.toml`, keys, logs, persona/memory copies or backups. See [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) before publishing.
