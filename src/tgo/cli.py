import asyncio

import typer
from pydantic import ValidationError
from rich.console import Console

from tgo.menu import TgoApp
from tgo.prompter import QuestionaryPrompter
from tgo.settings import ConfigError, Settings
from tgo.telegram import TelegramGateway
from tgo.text import TerminalText

app = typer.Typer(help="Tidy up your Telegram: run `tgo` for the menu.", add_completion=False)
console = Console()


def _settings() -> Settings:
    try:
        return Settings()
    except ValidationError as e:
        console.print(f"[red]Bad configuration in .env:[/red]\n{e}")
        raise typer.Exit(1)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context):
    """Open the menu."""
    if ctx.invoked_subcommand is not None:
        return
    settings = _settings()
    try:
        settings.require_api()
        TgoApp(settings, QuestionaryPrompter(TerminalText(settings.bidi)), console).run()
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
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
        raise typer.Exit(1)
    console.print("Logged out.")
