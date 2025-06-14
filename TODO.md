# Todo

## Phase 1: Foundation
- [x] **T001 · Chore · P0: scaffold project structure and dependencies**
    - **Context:** Implementation Plan > Phase 1 > Create package structure; Dependencies (Minimal)
    - **Action:**
        1. Create the full directory structure as defined in the `Architecture Overview` (`hacktivity/`, `core/`, `prompts/`, etc.).
        2. Create empty module files (`__init__.py`, `github.py`, `ai.py`, `cache.py`, `config.py`).
        3. Create `requirements.txt` and populate it with all specified dependencies (`click`, `pydantic`, `diskcache`, `rich`, etc.).
    - **Done‑when:**
        1. The directory tree matches the plan.
        2. `pip install -r requirements.txt` installs all dependencies successfully in a clean environment.
    - **Depends‑on:** none

- [x] **T002 · Refactor · P1: extract github data fetching logic to core/github.py**
    - **Context:** Implementation Plan > Phase 1 > Extract core functions into modules
    - **Action:**
        1. Move all logic for interacting with the GitHub API from the prototype into `hacktivity/core/github.py`.
        2. Define a clear function interface, such as `fetch_commits(user, org, repo, since, until)`.
    - **Done‑when:**
        1. All GitHub API interaction is isolated in `core/github.py`.
        2. The module can be imported and its functions called without error.
    - **Depends‑on:** [T001]

- [x] **T003 · Refactor · P1: extract ai summarization logic to core/ai.py**
    - **Context:** Implementation Plan > Phase 1 > Extract core functions into modules
    - **Action:**
        1. Move all logic for interacting with the AI provider into `hacktivity/core/ai.py`.
        2. Define a clear function interface, such as `get_summary(text, prompt)`.
    - **Done‑when:**
        1. All AI summarization logic is isolated in `core/ai.py`.
    - **Depends‑on:** [T001]

- [x] **T004 · Refactor · P1: implement cli entrypoint with click in hacktivity.py**
    - **Context:** Decision Log > Click over argparse
    - **Action:**
        1. Refactor `hacktivity.py` to be a minimal CLI entrypoint using `click`.
        2. Orchestrate calls to the new core modules (`github`, `ai`) to replicate the original prototype's functionality.
    - **Done‑when:**
        1. `hacktivity.py` uses `click` for argument parsing.
        2. Running the script with arguments correctly calls the core modules.
    - **Depends‑on:** [T002, T003]

- [x] **T005 · Feature · P1: implement basic retry logic for network calls**
    - **Context:** Key Enhancements > 1. Robust Error Handling
    - **Action:**
        1. Add a retry decorator (e.g., from the `tenacity` library) to the primary data fetching function in `core/github.py`.
        2. Configure it to retry on network-related exceptions with exponential backoff.
    - **Done‑when:**
        1. A transient network error during an API call triggers a retry instead of a crash.
        2. A unit test successfully simulates a timeout and asserts that retry attempts are made.
    - **Depends‑on:** [T002]

- [x] **T006 · Feature · P1: implement file-based caching module in core/cache.py**
    - **Context:** Key Enhancements > 2. Smart Caching
    - **Action:**
        1. Implement `get(key, max_age_hours)` and `set(key, value)` functions in `core/cache.py` using the `diskcache` library.
        2. Configure the cache to store files in the `~/.hacktivity/cache/` directory.
    - **Done‑when:**
        1. `core.cache.set()` stores a value to a file on disk.
        2. `core.cache.get()` retrieves a non-expired value and returns `None` for expired values.
    - **Depends‑on:** [T001]

- [x] **T007 · Refactor · P1: integrate caching into github data fetching**
    - **Context:** Key Enhancements > 2. Smart Caching
    - **Action:**
        1. In `core/github.py`, generate a unique cache key for each API query.
        2. Before fetching from the API, attempt to retrieve the result from the cache using `cache.get()`.
        3. If a fresh result is fetched, store it in the cache using `cache.set()`.
    - **Done‑when:**
        1. Running the same command twice results in the second run hitting the cache instead of the API.
    - **Verification:**
        1. Run a command for a specific date range.
        2. Immediately run it again and observe logs indicating a cache hit and a much faster execution time.
    - **Depends‑on:** [T002, T006]

## Phase 2: Reliability
- [x] **T008 · Feature · P1: handle api rate limit errors gracefully**
    - **Context:** Key Enhancements > 1. Robust Error Handling
    - **Action:**
        1. In `core/github.py`, catch the specific exception for a `RateLimitExceeded` error.
        2. Log an informative error message, including the reset time.
        3. Attempt to return a cached result if one is available for the query.
    - **Done‑when:**
        1. A rate limit error no longer crashes the tool.
        2. A unit test simulating a rate limit hit verifies that the cache is checked as a fallback.
    - **Depends‑on:** [T005, T007]

- [x] **T009 · Feature · P1: implement progress indicators for long operations**
    - **Context:** Key Enhancements > 5. Progress Indicators
    - **Action:**
        1. Integrate `rich.progress.Progress` into the pagination loop in `core/github.py`.
        2. Display a task bar labeled "Fetching commits" that updates after each page is fetched.
    - **Done‑when:**
        1. A progress bar appears in the console for any fetch operation that involves multiple pages.
    - **Verification:**
        1. Run a command for a long time range (e.g., a month).
        2. Observe the progress bar updating as data is fetched.
    - **Depends‑on:** [T002]

- [x] **T010 · Feature · P1: implement partial result caching to enable resume**
    - **Context:** Key Enhancements > 2. Smart Caching > Cache partial results
    - **Action:**
        1. In `core/cache.py`, implement `append_partial(key, batch)` and `get_partial(key)`.
        2. In `core/github.py`, call `cache.append_partial()` after each successful page/batch retrieval.
        3. Before starting a new fetch, check `cache.get_partial()` and adjust the API query to fetch only the remaining data.
    - **Done‑when:**
        1. An operation interrupted midway and then re-run will only fetch the missing data.
    - **Verification:**
        1. Start a long-running fetch and kill it (Ctrl-C) halfway through.
        2. Rerun the same command and observe from logs or progress bar that it resumes from where it left off.
    - **Depends‑on:** [T006, T007]

- [x] **T011 · Chore · P2: establish structured logging**
    - **Context:** Implementation Plan > Phase 2 > Create proper logging
    - **Action:**
        1. Configure Python's built-in `logging` module in `hacktivity.py` or a dedicated `core/logging.py`.
        2. Replace all `print()` calls used for debugging/info with `log.info()`, `log.warning()`, etc.
    - **Done‑when:**
        1. The application uses a standard logging framework for all operational messages.
        2. No `print()` statements remain for non-user-output purposes.
    - **Depends‑on:** [T004]

## Phase 3: Flexibility
- [x] **T012 · Feature · P1: implement configuration loading from toml file**
    - **Context:** Key Enhancements > 3. Configuration Management
    - **Action:**
        1. In `core/config.py`, define Pydantic models that mirror the structure of `config.toml`.
        2. Implement a function to load `~/.hacktivity/config.toml`, parse it, and populate the Pydantic models, providing sensible defaults.
        3. In `hacktivity.py`, load the configuration at startup and pass it to core modules.
    - **Done‑when:**
        1. The application's behavior (e.g., `max_commits`) changes based on values in `config.toml`.
        2. The tool functions correctly with default values if the config file is missing.
    - **Depends‑on:** [T001, T004]

- [x] **T013 · Feature · P2: implement customizable prompt loading**
    - **Context:** Key Enhancements > 4. Prompt Customization
    - **Action:**
        1. Create default prompt files (`standup.md`, etc.) inside the `hacktivity/prompts/` source directory.
        2. Implement a function that loads a prompt by name, first checking `~/.hacktivity/prompts/` and falling back to the packaged defaults.
        3. Add a `--prompt` option to the CLI to specify which prompt to use.
    - **Done‑when:**
        1. A user-created prompt in `~/.hacktivity/prompts/` overrides the built-in one.
    - **Verification:**
        1. Create a custom prompt `~/.hacktivity/prompts/test.md` with unique instructions (e.g., "Summarize as a pirate.").
        2. Run `hacktivity --prompt test` and verify the output format matches the unique instructions.
    - **Depends‑on:** [T001, T004]

- [x] **T014 · Feature · P2: add multiple output format options**
    - **Context:** Key Enhancements > 3. Configuration Management > [output]
    - **Action:**
        1. Add a `--format` option to the CLI with choices `markdown`, `json`, `plain`.
        2. Implement logic to format the final AI summary according to the selected format.
    - **Done‑when:**
        1. `hacktivity --format json` produces a valid JSON string.
        2. `hacktivity --format plain` produces text with no markdown formatting.
    - **Depends‑on:** [T004, T012]

- [x] **T015 · Chore · P2: create installation package with pyproject.toml**
    - **Context:** Implementation Plan > Phase 3 > Create installation package
    - **Action:**
        1. Create a `pyproject.toml` file configured for `setuptools`.
        2. Define the `[project.scripts]` entry point to map the `hacktivity` command to the main `click` function.
        3. Ensure the `prompts/` directory is included as package data.
    - **Done‑when:**
        1. `pip install .` installs the package and the `hacktivity` command is available in the shell.
    - **Depends‑on:** [T004, T013]

## Phase 4: Polish & Testing
- [x] **T016 · Test · P1: add unit tests for all core modules**
    - **Context:** Testing Strategy > Unit tests for core functions
    - **Action:**
        1. Create a `tests/` directory and configure `pytest`.
        2. Write unit tests for `core/cache.py`, `core/config.py`, and `core/github.py` (with mocked API calls).
    - **Done‑when:**
        1. Unit tests achieve ≥90% code coverage for the specified core modules.
        2. A CI pipeline is configured to run these tests automatically.
    - **Depends‑on:** [T006, T012]

- [x] **T017 · Feature · P2: implement `--init` command for user setup**
    - **Context:** Implementation Plan > Phase 4 > Add --init command for setup
    - **Action:**
        1. Add an `init` subcommand to the `click` CLI.
        2. This command should create the `~/.hacktivity` directory, a default `config.toml`, and copy the default prompts into `~/.hacktivity/prompts/`.
    - **Done‑when:**
        1. Running `hacktivity init` successfully scaffolds the user's configuration directory.
    - **Verification:**
        1. Delete the `~/.hacktivity` directory.
        2. Run `hacktivity init` and verify that the directory and all default files are created correctly.
    - **Depends‑on:** [T012, T013]

- [x] **T018 · Chore · P2: improve documentation in readme.md**
    - **Context:** Implementation Plan > Phase 4 > Improve documentation
    - **Action:**
        1. Update `README.md` to include full installation, configuration, and usage instructions for all new features.
        2. Add examples for prompt customization and output formats.
    - **Done‑when:**
        1. A new user can successfully install, configure, and run the tool using only the `README.md`.
    - **Depends‑on:** [T015, T017]

## Phase 5: Repository-First Architecture
- [x] **T019 · Feature · P0: implement repository discovery module**
    - **Context:** Architecture redesign to avoid GitHub Search API timeouts
    - **Action:**
        1. Create `core/repos.py` module for repository discovery
        2. Implement `discover_user_repositories()` using gh api /user/repos
        3. Add organization repository discovery
        4. Cache repository metadata with 7-day TTL
    - **Done‑when:**
        1. Can discover all repositories for a user
        2. Repository list is cached and reused
        3. Supports filtering by organization
    - **Depends‑on:** none

- [x] **T020 · Feature · P0: implement repository-based commit fetching**
    - **Context:** Replace unreliable search/commits with per-repository fetching
    - **Action:**
        1. Create `core/commits.py` module
        2. Implement `fetch_repo_commits(repo, since, until)` using /repos/{owner}/{repo}/commits
        3. Add pagination support for large repositories
        4. Filter commits by author after fetching
    - **Done‑when:**
        1. Can fetch all commits from a repository in date range
        2. Properly handles repositories with 10,000+ commits
        3. No timeout errors on large repositories
    - **Depends‑on:** [T019]

- [x] **T021 · Feature · P0: implement date range chunking**
    - **Context:** Break large date ranges into manageable chunks
    - **Action:**
        1. Add `create_date_chunks(since, until, max_days)` function
        2. Process each chunk independently
        3. Save chunk completion state for resume
        4. Aggregate results across chunks
    - **Done‑when:**
        1. Year-long date ranges are split into weekly chunks
        2. Each chunk processes independently
        3. Failed chunks can be retried without losing progress
    - **Depends‑on:** [T020]

## Phase 6: State Management & Resilience
- [x] **T022 · Feature · P0: implement operation state management**
    - **Context:** Support pause/resume for long-running operations
    - **Action:**
        1. Create `core/state.py` module using SQLite
        2. Track operation ID, repositories, progress, failures
        3. Implement checkpoint saving after each repository
        4. Add resume capability from saved state
    - **Done‑when:**
        1. Operations save state after each repository
        2. Can resume interrupted operations
        3. State includes retry counts and error details
    - **Depends‑on:** [T021]

- [x] **T023 · Feature · P1: implement multi-level caching**
    - **Context:** Different data types need different cache strategies
    - **Action:**
        1. Restructure cache into repos/, commits/, summaries/
        2. Implement different TTLs for each cache level
        3. Add cache size management and eviction
        4. Create cache warming strategies
    - **Done‑when:**
        1. Repository metadata cached for 7 days
        2. Commit data cached for 365 days
        3. Summaries cached for 30 days
    - **Depends‑on:** [T022]

- [x] **T024 · Feature · P1: add circuit breaker for API failures**
    - **Context:** Prevent cascading failures when GitHub API is degraded
    - **Action:**
        1. Implement circuit breaker pattern for each API endpoint
        2. Track failure rates per endpoint
        3. Add exponential backoff with jitter
        4. Implement fallback strategies
    - **Done‑when:**
        1. Circuit opens after 5 consecutive failures
        2. Automatic retry after cooldown period
        3. Falls back to cache when circuit is open
    - **Depends‑on:** [T020]

## Phase 7: Scale & Performance
- [x] **T025 · Feature · P1: implement parallel repository processing**
    - **Context:** Process multiple repositories concurrently for speed
    - **Action:**
        1. Add configurable parallel processing (max 5 repos)
        2. Implement thread-safe state updates
        3. Add progress tracking for parallel operations
        4. Handle rate limits across parallel requests
    - **Done‑when:**
        1. Can process 5 repositories in parallel
        2. Respects GitHub rate limits
        3. Progress bar shows aggregate progress
    - **Depends‑on:** [T022]

- [x] **T026 · Feature · P2: add GraphQL support for efficiency**
    - **Context:** GraphQL can fetch more data in fewer requests
    - **Action:**
        1. Implement GraphQL queries for repository data
        2. Add fallback to REST API when GraphQL fails
        3. Optimize queries to minimize API calls
        4. Handle GraphQL-specific rate limits
    - **Done‑when:**
        1. GraphQL used by default when available
        2. Automatic fallback to REST on errors
        3. Reduces API calls by 50%+
    - **Depends‑on:** [T019]

- [x] **T027 · Feature · P2: implement batch AI processing**
    - **Context:** Process commits in batches for AI efficiency
    - **Action:**
        1. Batch commits by repository or date chunk
        2. Implement configurable batch sizes
        3. Add batch-level caching
        4. Handle partial batch failures
    - **Done‑when:**
        1. AI processes 1000 commits per batch
        2. Failed batches can be retried
        3. Batch summaries are cached
    - **Depends‑on:** [T023]

## Phase 8: Testing & Documentation
- [x] **T028 · Test · P1: add integration tests for large-scale operations**
    - **Context:** Ensure robustness at scale
    - **Action:**
        1. Create test fixtures with 10,000+ commits
        2. Test resume capability with interruptions
        3. Test circuit breaker behavior
        4. Test parallel processing edge cases
    - **Done‑when:**
        1. Tests cover all failure scenarios
        2. Tests verify data completeness
        3. Performance benchmarks established
    - **Depends‑on:** [T025]

- [x] **T029 · Chore · P2: update documentation for new architecture**
    - **Context:** Document repository-first approach
    - **Action:**
        1. Update README with new capabilities
        2. Document resume functionality
        3. Add troubleshooting for long operations
        4. Create architecture decision records
    - **Done‑when:**
        1. Users understand new robustness features
        2. Clear guidance on handling large datasets
        3. Architecture decisions documented
    - **Depends‑on:** [T028]

## Phase 9: Enhanced User Experience & Output Quality

- [x] **T030 · Feature · P0: implement --debug flag and clean default output**
    - **Context:** Current output is cluttered with debug logs, making it hard to parse the actual summary
    - **Action:**
        1. Add `--debug` flag to CLI using click's built-in boolean option
        2. Modify all logger calls to respect debug level (INFO and above by default, DEBUG only with flag)
        3. Add clean progress indicators for non-debug mode
        4. Remove or reduce verbose caching and processing messages in default mode
    - **Done‑when:**
        1. Default output shows only progress indicators and final summary
        2. `--debug` flag shows current detailed logging
        3. Non-debug output is clean and professional
    - **Depends‑on:** none

- [x] **T031 · Feature · P1: add repository-aware progress indicators**
    - **Context:** Current progress shows batch numbers, but users think in terms of repositories
    - **Action:**
        1. Extract repository names from commit data before processing
        2. Replace batch progress with repository progress ("Processing repo X/Y")
        3. Add Rich progress bar with repository names and commit counts
        4. Show aggregate progress across all repositories
    - **Done‑when:**
        1. Progress bar shows "Processing repository-name (X commits)"
        2. Total progress reflects repository completion, not batch completion
        3. Clean, informative progress display in non-debug mode
    - **Depends‑on:** none

- [x] **T032 · Refactor · P1: modify commit grouping to be repository-first**
    - **Context:** Current batch processing groups commits arbitrarily, losing repository context
    - **Action:**
        1. Update `fetch_commits` to return commits grouped by repository
        2. Modify data structure to be `Dict[str, List[str]]` (repo -> commits)
        3. Update `_split_commits_into_batches` to work within repository boundaries
        4. Preserve repository metadata through the processing pipeline
    - **Done‑when:**
        1. Commits are grouped by repository before AI processing
        2. Repository names are preserved through the entire pipeline
        3. Batch splitting respects repository boundaries
    - **Depends‑on:** none

- [~] **T033 · Feature · P1: implement repository-level AI summarization**
    - **Context:** Repository context is lost when commits are mixed across repos in batches
    - **Action:**
        1. Create `get_repository_summary(repo_name, commits, prompt)` function
        2. Process each repository's commits as a cohesive unit
        3. Update cache keys to include repository name for repo-level caching
        4. Modify `get_batched_summary` to call repository-level processing
    - **Done‑when:**
        1. Each repository's commits are summarized together with repository context
        2. Repository summaries are cached separately with repo-specific keys
        3. Repository name is included in AI prompts for better context
    - **Depends‑on:** [T032]

- [ ] **T034 · Feature · P1: implement repository-aware aggregation logic**
    - **Context:** Final aggregation should preserve repository structure and context
    - **Action:**
        1. Update `_aggregate_batch_summaries` to be `_aggregate_repository_summaries`
        2. Modify aggregation prompt to understand repository-specific summaries
        3. Ensure final summary maintains repository organization
        4. Add repository count and commit distribution to aggregation context
    - **Done‑when:**
        1. Aggregation preserves repository boundaries and context
        2. Final summary includes repository-specific insights
        3. Repository structure is maintained in the final output
    - **Depends‑on:** [T033]

- [ ] **T035 · Feature · P1: update prompts for personal activity focus**
    - **Context:** Current prompts mention "team accomplishments" when processing individual commits
    - **Action:**
        1. Update default prompts to use personal language ("your accomplishments", "your work")
        2. Remove corporate-speak and generic team language
        3. Add repository context to prompts ("In repository X, you...")
        4. Make prompts more specific and actionable
    - **Done‑when:**
        1. Prompts reflect individual activity rather than team activity
        2. Repository names are incorporated into prompt context
        3. Language is personal, specific, and developer-focused
    - **Depends‑on:** none

- [ ] **T036 · Feature · P1: implement repository-structured output formatting**
    - **Context:** Output should clearly show activity organized by repository
    - **Action:**
        1. Create output formatter that structures content by repository
        2. Add repository headers with commit counts
        3. Group activities under repository sections
        4. Add summary section with key themes across repositories
    - **Done‑when:**
        1. Output clearly shows repository sections with activities
        2. Each repository section shows its specific accomplishments
        3. Final summary includes cross-repository themes and insights
    - **Verification:**
        1. Run summary command and verify repository sections are clearly delineated
        2. Verify repository names and commit counts are accurate
    - **Depends‑on:** [T033, T034]

- [ ] **T037 · Feature · P2: add output customization configuration**
    - **Context:** Users may want different levels of detail and organization
    - **Action:**
        1. Add configuration options for output verbosity (summary, detailed, full)
        2. Add option to show/hide repository sections
        3. Add option to customize output format templates
        4. Add CLI flags to override configuration settings
    - **Done‑when:**
        1. Users can configure output detail level in config.toml
        2. CLI flags like `--format detailed` override configuration
        3. Different output formats maintain repository structure
    - **Depends‑on:** [T036]
