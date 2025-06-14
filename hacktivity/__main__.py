#!/usr/bin/env python3
"""CLI entry point for hacktivity."""

import datetime
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

import click

from hacktivity.core.ai import check_ai_prerequisites, get_batched_summary, get_repository_aware_summary
from hacktivity.core.github import check_github_prerequisites, get_github_user, fetch_commits, fetch_commits_by_repository
from hacktivity.core.logging import setup_logging, get_logger
from hacktivity.core.config import get_config, save_default_config

logger = get_logger(__name__)


def load_prompts() -> Dict[str, str]:
    """Load prompt templates from user and default directories.
    
    First checks ~/.hacktivity/prompts/ for user-defined prompts,
    then loads default prompts from the package directory.
    User prompts override defaults with the same name.
    """
    prompts = {}
    
    # First, load default prompts from package directory
    default_prompts_dir = Path(__file__).parent / "prompts"
    for prompt_file in default_prompts_dir.glob("*.md"):
        prompt_name = prompt_file.stem
        prompts[prompt_name] = prompt_file.read_text().strip()
        logger.debug(f"Loaded default prompt: {prompt_name}")
    
    # Then, check for user-defined prompts (these override defaults)
    user_prompts_dir = Path.home() / ".hacktivity" / "prompts"
    if user_prompts_dir.exists():
        for prompt_file in user_prompts_dir.glob("*.md"):
            prompt_name = prompt_file.stem
            prompts[prompt_name] = prompt_file.read_text().strip()
            logger.info(f"Loaded user prompt: {prompt_name} (overrides default)")
    
    return prompts


def get_prompt_descriptions() -> Dict[str, str]:
    """Get human-readable descriptions for each prompt type."""
    return {
        "standup": "A concise summary for a daily stand-up meeting.",
        "retro": "A more detailed analysis for a team retrospective.",
        "weekly": "A summary suitable for a weekly team newsletter."
    }


def format_output(summary: str, format_type: str, metadata: Dict[str, str]) -> str:
    """Format the summary according to the specified output format.
    
    Args:
        summary: The AI-generated summary text
        format_type: The output format ('markdown', 'json', 'plain')
        metadata: Additional metadata (dates, user, etc.)
    
    Returns:
        Formatted output string
    """
    if format_type == "json":
        output = {
            "summary": summary,
            "metadata": metadata
        }
        return json.dumps(output, indent=2)
    
    elif format_type == "plain":
        # Strip markdown formatting for plain text
        plain_summary = summary
        # Remove common markdown elements
        plain_summary = plain_summary.replace("**", "")
        plain_summary = plain_summary.replace("*", "")
        plain_summary = plain_summary.replace("```", "")
        plain_summary = plain_summary.replace("`", "")
        plain_summary = plain_summary.replace("# ", "")
        plain_summary = plain_summary.replace("## ", "")
        plain_summary = plain_summary.replace("### ", "")
        
        # Build plain text output
        lines = [
            "Git Activity Summary",
            "===================",
            f"User: {metadata['user']}",
            f"Period: {metadata['since']} to {metadata['until']}",
            f"Prompt: {metadata['prompt_type']}",
            "",
            plain_summary,
            "==================="
        ]
        return "\n".join(lines)
    
    else:  # markdown (default)
        lines = [
            "\n--- Git Activity Summary ---",
            summary,
            "--------------------------"
        ]
        return "\n".join(lines)


@click.group(invoke_without_command=True)
@click.option(
    "--since",
    type=str,
    help="The start date in YYYY-MM-DD format."
)
@click.option(
    "--until", 
    type=str,
    help="The end date in YYYY-MM-DD format."
)
@click.option(
    "--type",
    "prompt_type",
    type=click.Choice(["standup", "retro", "weekly"]),
    default=None,  # Will use config default
    help="The type of summary to generate (deprecated, use --prompt)."
)
@click.option(
    "--prompt",
    "prompt_name",
    type=str,
    default=None,
    help="Name of the prompt to use (e.g., 'standup', 'retro', or custom)."
)
@click.option(
    "--org",
    help="Filter activity to a specific GitHub organization."
)
@click.option(
    "--repo", 
    help="Filter activity to a specific repository (e.g., 'owner/repo-name')."
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "json", "plain"]),
    default=None,  # Will use config default
    help="Output format for the summary (default: from config or markdown)."
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug logging output."
)
@click.pass_context
def cli(ctx, since, until, prompt_type, prompt_name, org, repo, output_format, debug):
    """Summarize your GitHub activity using the Gemini API."""
    if ctx.invoked_subcommand is None:
        # If no subcommand is provided, run the summary with the provided options
        ctx.invoke(summary, since=since, until=until, prompt_type=prompt_type, 
                  prompt_name=prompt_name, org=org, repo=repo, output_format=output_format, debug=debug)


@cli.command()
@click.option(
    "--since",
    type=str,
    help="The start date in YYYY-MM-DD format."
)
@click.option(
    "--until", 
    type=str,
    help="The end date in YYYY-MM-DD format."
)
@click.option(
    "--type",
    "prompt_type",
    type=click.Choice(["standup", "retro", "weekly"]),
    default=None,  # Will use config default
    help="The type of summary to generate (deprecated, use --prompt)."
)
@click.option(
    "--prompt",
    "prompt_name",
    type=str,
    default=None,
    help="Name of the prompt to use (e.g., 'standup', 'retro', or custom)."
)
@click.option(
    "--org",
    help="Filter activity to a specific GitHub organization."
)
@click.option(
    "--repo", 
    help="Filter activity to a specific repository (e.g., 'owner/repo-name')."
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "json", "plain"]),
    default=None,  # Will use config default
    help="Output format for the summary (default: from config or markdown)."
)
@click.option(
    "--debug",
    is_flag=True,
    default=False,
    help="Enable debug logging output."
)
def summary(
    since: Optional[str],
    until: Optional[str], 
    prompt_type: Optional[str],
    prompt_name: Optional[str],
    org: Optional[str],
    repo: Optional[str],
    output_format: Optional[str],
    debug: bool = False
) -> None:
    """Summarize your GitHub activity using the Gemini API."""
    
    # Load configuration
    config = get_config()
    
    # Initialize logging with debug flag
    setup_logging(debug=debug)
    
    # Determine output format
    if output_format is None:
        output_format = config.app.default_format
        logger.debug("Using default format from config: %s", output_format)
    
    # Determine which prompt to use
    # Priority: --prompt > --type > config default
    selected_prompt = None
    if prompt_name is not None:
        selected_prompt = prompt_name
        if prompt_type is not None:
            logger.warning("Both --prompt and --type specified; using --prompt='%s'", prompt_name)
    elif prompt_type is not None:
        selected_prompt = prompt_type
        logger.debug("Using --type='%s' (deprecated, consider using --prompt)", prompt_type)
    else:
        selected_prompt = config.app.default_prompt_type
        logger.debug("Using default prompt type from config: %s", selected_prompt)
    
    # Set date defaults
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    last_week = today - datetime.timedelta(weeks=1)
    
    if since is None:
        since = yesterday.isoformat()
    if until is None:
        until = today.isoformat()
        
    # Special logic for retro type
    if selected_prompt == 'retro' and since == yesterday.isoformat():
        since = last_week.isoformat()
        logger.info("'retro' prompt selected, defaulting --since to one week ago: %s", since)
    
    # Check prerequisites
    check_github_prerequisites()
    check_ai_prerequisites()
    
    # Get GitHub user
    github_user = get_github_user()
    
    # Fetch commits grouped by repository
    repo_commits = fetch_commits_by_repository(github_user, since, until, org, repo)
    
    if not repo_commits:
        print("\nNo activity found for the selected criteria.", file=sys.stderr)
        return
    
    # Load prompts and get summary
    prompts = load_prompts()
    if selected_prompt not in prompts:
        available = ", ".join(sorted(prompts.keys()))
        print(f"Error: Prompt '{selected_prompt}' not found.", file=sys.stderr)
        print(f"Available prompts: {available}", file=sys.stderr)
        sys.exit(1)
        
    summary = get_repository_aware_summary(repo_commits, prompts[selected_prompt])
    
    # Prepare metadata for formatting
    metadata = {
        "user": github_user,
        "since": since,
        "until": until,
        "prompt_type": selected_prompt,
        "org": org or "all",
        "repo": repo or "all"
    }
    
    # Format and output results
    formatted_output = format_output(summary, output_format, metadata)
    print(formatted_output)


def copy_default_prompts():
    """Copy default prompt files to user directory."""
    # Get source and destination directories
    package_prompts_dir = Path(__file__).parent / "prompts"
    user_prompts_dir = Path.home() / ".hacktivity" / "prompts"
    
    # Create user prompts directory
    user_prompts_dir.mkdir(parents=True, exist_ok=True)
    
    # Copy each prompt file
    copied_count = 0
    for prompt_file in package_prompts_dir.glob("*.md"):
        dest_file = user_prompts_dir / prompt_file.name
        
        if dest_file.exists():
            click.echo(f"Prompt file already exists: {dest_file}")
        else:
            shutil.copy2(prompt_file, dest_file)
            click.echo(f"Copied prompt: {dest_file}")
            copied_count += 1
    
    if copied_count > 0:
        click.echo(f"Copied {copied_count} default prompt files to {user_prompts_dir}")
    else:
        click.echo("All prompt files already exist.")


@cli.command()
def init():
    """Initialize hacktivity configuration and user directory."""
    click.echo("Initializing hacktivity configuration...")
    
    # Create ~/.hacktivity directory and default config
    try:
        save_default_config()
        click.echo("✓ Created default configuration file")
    except Exception as e:
        click.echo(f"✗ Failed to create configuration: {e}", err=True)
        return
    
    # Copy default prompts
    try:
        copy_default_prompts()
        click.echo("✓ Set up default prompts")
    except Exception as e:
        click.echo(f"✗ Failed to copy prompts: {e}", err=True)
        return
    
    click.echo("\nInitialization complete! 🎉")
    click.echo("\nNext steps:")
    click.echo("1. Set your GITHUB_TOKEN environment variable")
    click.echo("2. Set your GEMINI_API_KEY environment variable") 
    click.echo("3. Run 'hacktivity summary' to generate your first activity summary")
    click.echo("4. Customize prompts in ~/.hacktivity/prompts/ as needed")
    click.echo("5. Adjust settings in ~/.hacktivity/config.toml if desired")


# Create an alias for backward compatibility
def main():
    """Entry point that preserves backward compatibility."""
    return cli()


if __name__ == "__main__":
    main()
