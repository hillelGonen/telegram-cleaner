import asyncio
import sys

import questionary
import typer
from pydantic import ValidationError
from rich.console import Console

from tgo.menu import TgoApp
from tgo.prompter import QuestionaryPrompter
from tgo.settings import API_HASH_RE, ConfigError, Settings, save_credentials
from tgo.telegram import TelegramGateway
from tgo.text import TerminalText

app = typer.Typer(help="Tidy up your Telegram: run `tgo` for the menu.", add_completion=False)
console = Console()


def _settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        console.print(f"[red]Bad configuration in .env:[/red]\n{e}")
        raise typer.Exit(1) from None


def _first_run_setup() -> bool:
    """Ask for the Telegram API id/hash once and save them. False if cancelled."""
    console.print(
        "\n[bold]First run: connect tgo to Telegram[/bold]\n"
        "1. Open https://my.telegram.org and log in with your phone number\n"
        "2. Go to [bold]API development tools[/bold] and create an app (any name)\n"
        "3. Copy the [bold]api_id[/bold] and [bold]api_hash[/bold] here\n"
    )
    api_id = questionary.text(
        "api_id:", validate=lambda v: v.strip().isdigit() or "Numbers only").ask()
    if api_id is None:
        return False
    api_hash = questionary.password(
        "api_hash:", validate=lambda v: bool(API_HASH_RE.match(v.strip())) or "32 characters, 0-9 a-f",
    ).ask()
    if api_hash is None:
        return False
    path = save_credentials(int(api_id.strip()), api_hash.strip())
    console.print(f"[green]Saved to {path}[/green] (only you can read it)\n")
    return True


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Open the menu."""
    if ctx.invoked_subcommand is not None:
        return
    settings = _settings()
    try:
        try:
            settings.require_api()
        except ConfigError:
            if not sys.stdin.isatty() or not _first_run_setup():
                raise
            settings = _settings()
            settings.require_api()
        TgoApp(settings, QuestionaryPrompter(TerminalText(settings.bidi)), console).run()
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        console.print("\nBye.")


@app.command()
def logout():
    """Log out of Telegram and delete the local session."""
    settings = _settings()

    async def go():
        async with TelegramGateway(settings) as tg:
            await tg.logout()

    try:
        asyncio.run(go())
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    console.print("Logged out.")
