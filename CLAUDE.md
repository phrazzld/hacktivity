# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Python CLI tool called "hacktivity" that summarizes GitHub activity using the Gemini AI API. It fetches commit messages from GitHub and uses AI to generate structured summaries for different contexts (daily standups, team retrospectives).

## Key Dependencies

- `google-generativeai` - For Gemini API integration
- `gh` CLI - GitHub command line tool (must be installed and authenticated)
- Python 3 standard library modules

## Required Environment Setup

- Install dependencies: `pip install google-generativeai`
- Install GitHub CLI: https://cli.github.com/
- Authenticate with GitHub: `gh auth login`
- Set environment variable: `export GEMINI_API_KEY='your_api_key'`

## Common Commands

- Run the tool: `python3 main.py`
- Generate standup summary: `python3 main.py --type standup`
- Generate retrospective: `python3 main.py --type retro`
- Filter by org: `python3 main.py --org organization-name`
- Filter by repo: `python3 main.py --repo owner/repo-name`
- Custom date range: `python3 main.py --since 2024-01-01 --until 2024-01-31`

## Architecture

The application follows a linear workflow:
1. **Prerequisites Check** (`check_prerequisites()`) - Validates all required tools and credentials
2. **User Authentication** (`get_github_user()`) - Gets authenticated GitHub username
3. **Data Fetching** (`fetch_github_activity()`) - Uses GitHub Search API to find commits
4. **AI Processing** (`summarize_with_gemini()`) - Sends commit data to Gemini for summarization

## Prompt System

The tool uses a configurable prompt system defined in the `PROMPTS` dictionary. Each prompt type has:
- `description` - Human-readable explanation
- `system_prompt` - Detailed instructions for the AI model

Current prompt types:
- `standup` - Concise daily summary
- `retro` - Detailed retrospective analysis

## Error Handling

The application includes comprehensive error handling for:
- Missing dependencies and tools
- Authentication failures
- GitHub API errors and timeouts
- Gemini API communication issues