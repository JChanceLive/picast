# Contributing to PiCast

Thanks for wanting to contribute! PiCast is a straightforward project and contributions are welcome.

## Development Setup

```bash
git clone https://github.com/JChanceLive/picast.git
cd picast
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,tui,telegram]"
```

## Running Tests

```bash
pytest tests/ -v
```

Tests run against mock components - no Pi or mpv required.

## Code Style

We use [ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
ruff check src/ tests/
ruff format src/ tests/
```

Target: Python 3.9+, 100 char line length.

## Making Changes

1. Fork the repo and create a branch
2. Make your changes
3. Add tests for new functionality
4. Run the full test suite
5. Open a PR

## Architecture

PiCast has a clean layered architecture:

- **Server** (`src/picast/server/`) - Flask API, mpv control, queue, library, sources
- **TUI** (`src/picast/tui/`) - Textual terminal dashboard
- **Web UI** (`src/picast/server/templates/`) - Browser interface
- **Telegram** (`src/picast/server/telegram_bot.py`) - Remote control bot

All clients (TUI, Web, Telegram) talk to the same REST API. The server is the single source of truth.

## Adding a New Source

To add support for a new media source (e.g., SoundCloud):

1. Create `src/picast/server/sources/soundcloud.py`
2. Implement `SourceHandler` base class from `sources/base.py`
3. Register in `sources/__init__.py`
4. Add tests in `tests/test_sources.py`

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
