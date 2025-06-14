Okay, here's a technical overview of the `/Users/phaedrus/Development/hacktivity/hacktivity` directory, focusing on the provided `__main__.py` file and incorporating information from the directory structure and other files you've already described.

**Technical Overview: `/Users/phaedrus/Development/hacktivity/hacktivity`**

**Purpose:**

The `/Users/phaedrus/Development/hacktivity/hacktivity` directory represents the root directory of the Hacktivity application. This application is designed to summarize GitHub activity using the Gemini AI model. It provides a command-line interface (CLI) for fetching commit data from GitHub, feeding it to the AI model with configurable prompts, and formatting the resulting summary in various output formats.  The application aims to automate the process of generating reports and summaries for purposes such as team retrospectives, daily stand-ups, and weekly newsletters.

**Architecture:**

The application adopts a modular architecture, with core functionality encapsulated in the `core` subdirectory and prompt templates stored in the `prompts` subdirectory.  The `__main__.py` file serves as the CLI entry point, using the `click` library to define commands and options. The application leverages configuration files to manage settings such as API keys, default output formats, and prompt types. It also supports user-defined prompt templates, allowing for customization of the summary generation process.

**Key File Roles:**

*   **`__main__.py`:** This file is the main entry point for the Hacktivity CLI. Its key responsibilities are:

    *   **CLI Definition:** Defines the `cli` click group and the `summary` and `init` commands, along with their respective options. The `cli` group allows the user to either specify a subcommand or directly run the `summary` command with options.
    *   **Configuration Loading:** Loads application configuration from a TOML file using `hacktivity.core.config.get_config()`.
    *   **Logging Initialization:** Sets up logging using `hacktivity.core.logging.setup_logging()`.
    *   **Prerequisite Checks:** Checks for the presence of required tools (GitHub CLI) and environment variables (API keys).
    *   **GitHub Data Fetching:** Uses `hacktivity.core.github.fetch_commits()` to retrieve commit data from GitHub.
    *   **AI Summarization:** Uses `hacktivity.core.ai.get_summary()` to generate a summary of the commit data using the configured prompt.
    *   **Prompt Loading:** Loads prompt templates from the `prompts` directory (both default and user-defined).
    *   **Output Formatting:** Formats the summary in the specified output format (Markdown, JSON, or plain text).
    *   **Error Handling:** Handles errors such as missing prompts and missing API keys.
    *   **`init` Command:**  Provides an `init` command to set up the default configuration file and copy default prompt templates to the user's home directory.
    *   **Backward Compatibility:** Includes a `main()` function to provide backward compatibility for older entry points.

*   **`core` Directory (as described previously):** Contains the core modules responsible for GitHub API interaction, AI summarization, data caching, configuration management, and state persistence.

*   **`prompts` Directory (as described previously):** Contains default prompt templates used to guide the language model during summarization.

**Important Dependencies and Gotchas:**

*   **`click`:** The application relies on the `click` library for defining the command-line interface.
*   **Environment Variables:**  The application depends on the `GITHUB_TOKEN` and `GEMINI_API_KEY` environment variables being set.
*   **Configuration File:** The application uses a TOML configuration file (`~/.hacktivity/config.toml`) to store settings.
*   **Prompt Loading Order:**  The application loads prompts from both the package directory and the user's home directory, with user-defined prompts overriding the defaults.
*   **Date Handling:**  The application uses the `datetime` module for handling dates and date ranges.
*   **Error Handling:**  The application includes error handling for various scenarios, such as missing API keys, invalid prompts, and GitHub API errors.
*   **Backward Compatibility:**  The `main()` function is included to maintain backward compatibility with older entry points.
*   **Default Values:** The application uses default values for various options, such as the output format and prompt type, if they are not explicitly specified by the user.
*   **Prompt Selection Logic:** The application has a specific order of precedence for determining which prompt to use: command-line `--prompt` option, command-line `--type` option (deprecated), and the `default_prompt_type` setting in the configuration file.
