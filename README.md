# LatoDash

LatoDash is a loopback-only dashboard for a LatoChan server. It shows system metrics, logs, and Ollama status, and offers tools for managing the bot through SSH. The frontend uses plain JavaScript and CSS.

## Try it

On Windows, run `LatoDash demo.bat`. On Linux or macOS with Python 3.11 or later:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m latodash --demo --no-browser
```

Open the address printed by the launcher. Demo mode uses synthetic data and bundled rules.

## Connect to a server

See [Getting Started](GETTING_STARTED.md) and [the sample config](config.example.toml). Keep the working config and SSH credentials private. Use a dedicated SSH key, pin the server host key, and review the restricted sudo rules in `server/` before installing the matching helpers.

## Development

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check --select E9,F .
```

## License

[MIT](LICENSE).
