# Hacktivity

**Summarize your GitHub activity using the Gemini API**

Hacktivity is a command-line tool that automatically generates summaries of your GitHub activity. Perfect for daily standups, weekly reports, and team retrospectives.

## Features

- 🚀 **Smart caching** - Fast subsequent runs with automatic resume capability
- 📝 **Multiple output formats** - Markdown, JSON, and plain text
- 🎯 **Customizable prompts** - Built-in templates or create your own
- ⚙️ **Flexible configuration** - TOML-based config with sensible defaults
- 🔄 **Robust error handling** - Automatic retries and rate limit management
- 📊 **Progress indicators** - Visual feedback for long operations
- 🔍 **Filtering options** - Focus on specific organizations or repositories
- ⚡ **Parallel processing** - Process multiple repositories concurrently for speed
- 🛡️ **Circuit breaker protection** - Isolated failure handling per repository
- 🔗 **Repository-first architecture** - Reliable processing of large datasets
- ⏸️ **Resume capability** - Automatic recovery from interruptions

## Installation

### From PyPI (Recommended)

```bash
pip install hacktivity
```

### From Source

```bash
git clone https://github.com/phrazzld/hacktivity.git
cd hacktivity
pip install .
```

## Quick Start

1. **Initialize configuration**:
   ```bash
   hacktivity init
   ```

2. **Set up environment variables**:
   ```bash
   export GITHUB_TOKEN="your_github_token"
   export GEMINI_API_KEY="your_gemini_api_key"
   ```

3. **Generate your first summary**:
   ```bash
   hacktivity
   ```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_TOKEN` | ✅ | GitHub personal access token |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |

### Getting API Keys

**GitHub Token:**
1. Go to [GitHub Settings > Developer settings > Personal access tokens](https://github.com/settings/tokens)
2. Click "Generate new token (classic)"
3. Select scopes: `repo`, `user:email`
4. Copy the generated token

**Gemini API Key:**
1. Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Create a new API key
3. Copy the key

## Usage

### Basic Commands

```bash
# Generate a summary (default: yesterday's activity)
hacktivity

# Explicit summary command
hacktivity summary

# Initialize user configuration
hacktivity init

# Show help
hacktivity --help
```

### Date Range Options

```bash
# Specific date range
hacktivity --since 2024-01-01 --until 2024-01-07

# Last week (automatic for retro prompt)
hacktivity --prompt retro

# Custom date range for retro
hacktivity --prompt retro --since 2024-01-01
```

### Output Formats

```bash
# Markdown (default)
hacktivity --format markdown

# JSON for programmatic use
hacktivity --format json

# Plain text (no formatting)
hacktivity --format plain
```

### Filtering

```bash
# Filter by organization
hacktivity --org mycompany

# Filter by specific repository
hacktivity --repo mycompany/myproject

# Combine filters
hacktivity --org mycompany --repo mycompany/frontend
```

### Prompt Types

```bash
# Daily standup summary
hacktivity --prompt standup

# Weekly retrospective
hacktivity --prompt retro

# Weekly newsletter format
hacktivity --prompt weekly

# Custom prompt
hacktivity --prompt my-custom-prompt
```

## Configuration

Hacktivity uses a TOML configuration file located at `~/.hacktivity/config.toml`. Run `hacktivity init` to create it with defaults.

### Example Configuration

```toml
[cache]
max_age_hours = 24      # Cache TTL (1-168 hours)
max_size_mb = 100       # Cache size limit (10-1000 MB)

[github]
per_page = 100          # Items per API page (1-100)
timeout_seconds = 60    # Request timeout (10-300 seconds)
max_pages = 10          # Maximum pages to fetch (1-20)
retry_attempts = 3      # Number of retry attempts (1-10)
retry_min_wait = 1      # Minimum retry wait time (seconds)
retry_max_wait = 60     # Maximum retry wait time (seconds)

# Parallel Processing Configuration
max_workers = 5         # Maximum concurrent repository workers (1-10)
rate_limit_buffer = 100 # API calls to reserve as buffer (50-500)
parallel_enabled = true # Enable parallel processing

# Circuit Breaker Configuration
cb_failure_threshold = 5    # Failures before circuit opens (1-20)
cb_cooldown_sec = 300      # Cooldown period in seconds (60-3600)

[ai]
model_name = "gemini-1.5-flash"  # AI model to use

[app]
log_level = "INFO"              # Log level: DEBUG, INFO, WARNING, ERROR
default_prompt_type = "standup" # Default prompt: standup, retro, weekly
default_format = "markdown"     # Default output: markdown, json, plain
```

## Prompt Customization

### Built-in Prompts

- **standup**: Concise summary for daily stand-up meetings
- **retro**: Detailed analysis for team retrospectives
- **weekly**: Summary suitable for weekly team newsletters

### Custom Prompts

1. **Create custom prompt**:
   ```bash
   mkdir -p ~/.hacktivity/prompts
   echo "Summarize this activity like a pirate would!" > ~/.hacktivity/prompts/pirate.md
   ```

2. **Use custom prompt**:
   ```bash
   hacktivity --prompt pirate
   ```

### Prompt Variables

Your prompts can reference the following context that will be automatically included:
- Commit messages and details
- Pull request activity
- Issue activity
- Date range information

## Output Examples

### Markdown Format
```markdown
--- Git Activity Summary ---
## Today's Achievements

- **Feature Development**: Implemented user authentication system
  - Added login/logout functionality
  - Created user profile management

- **Bug Fixes**: Resolved critical performance issues
  - Fixed memory leak in data processing
  - Optimized database queries

## Next Steps
- Deploy authentication system to staging
- Begin work on notification features
--------------------------
```

### JSON Format
```json
{
  "summary": "## Today's Achievements...",
  "metadata": {
    "user": "username",
    "since": "2024-01-15",
    "until": "2024-01-16",
    "prompt_type": "standup",
    "org": "all",
    "repo": "all"
  }
}
```

### Plain Text Format
```
Git Activity Summary
===================
User: username
Period: 2024-01-15 to 2024-01-16
Prompt: standup

Today's Achievements

Feature Development: Implemented user authentication system
- Added login/logout functionality
- Created user profile management

Bug Fixes: Resolved critical performance issues
- Fixed memory leak in data processing
- Optimized database queries
===================
```

## Advanced Features

### Repository-First Architecture

Hacktivity uses a robust repository-first approach for enterprise-scale processing:
- **Repository discovery**: Efficiently discovers all accessible repositories
- **Per-repository processing**: Avoids unreliable search API limitations
- **Date range chunking**: Breaks large time periods into manageable chunks
- **State tracking**: Maintains processing state for each repository

### Parallel Processing

Process multiple repositories concurrently for maximum efficiency:
- **Configurable workers**: 1-10 concurrent repository processors (default: 5)
- **Rate limit coordination**: Global rate limiting across all workers
- **Thread-safe operations**: Safe concurrent access to state and cache
- **Automatic fallback**: Falls back to sequential processing when needed

### Smart Caching & Resume Capability

Advanced caching system with automatic interruption recovery:
- **Multi-level caching**: Repositories cached for 7 days, commits for 365 days
- **Partial results**: Interrupted operations save progress automatically
- **Resume on restart**: Automatically continues from last successful state
- **Cache location**: `~/.hacktivity/cache/` and `~/.hacktivity/state.db`

### Circuit Breaker Protection

Isolated failure handling prevents cascade failures:
- **Per-endpoint isolation**: Each repository/API endpoint has independent protection
- **Automatic recovery**: Circuits automatically reset after cooldown period
- **Graceful degradation**: Falls back to cached data when circuits are open
- **Configurable thresholds**: Customize failure limits and cooldown periods

### Automatic Retry & Error Handling

Robust network handling with intelligent retry strategies:
- **Exponential backoff**: Automatic retries with increasing delays and jitter
- **Rate limit coordination**: Global 5000 req/hour GitHub limit respected
- **Timeout management**: Configurable timeouts with fallback to cache
- **Error isolation**: Single repository failures don't affect others

### Progress Indicators & Monitoring

Comprehensive feedback for long-running operations:
- **Real-time progress**: Live updates during repository processing
- **Aggregate tracking**: Combined progress across parallel workers
- **Performance metrics**: Memory usage and throughput monitoring
- **Operation state**: Detailed status for each repository and chunk

## Troubleshooting

### Common Issues

**"No activity found"**
- Check your date range with `--since` and `--until`
- Verify you have commits in the specified time period
- Ensure your GitHub token has access to relevant repositories

**API Rate Limits**
- Hacktivity automatically handles rate limits with global coordination
- Cached results are used when possible
- Adjust `rate_limit_buffer` in config to reserve more API calls
- Consider reducing `max_pages` or `max_workers` for slower processing

**Authentication Errors**
- Verify `GITHUB_TOKEN` is set correctly
- Ensure token has required scopes (`repo`, `user:email`)
- Check token expiration in GitHub settings

**Gemini AI Errors**
- Verify `GEMINI_API_KEY` is set correctly
- Check API key is valid in Google AI Studio
- Ensure you have API quota available

### Large Dataset Processing

**Processing Many Repositories (100+)**
- Enable parallel processing: `parallel_enabled = true`
- Increase workers if needed: `max_workers = 8` (up to 10)
- Monitor rate limiting: increase `rate_limit_buffer` if needed
- Use organization filtering: `--org mycompany` to focus scope

**Long Date Ranges (6+ months)**
- Processing automatically chunks large date ranges
- Each chunk is processed independently for robustness  
- Failed chunks are automatically retried
- Progress is saved and can be resumed if interrupted

**Large Repositories (10,000+ commits)**
- Repository-first architecture handles large repos efficiently
- Commits are paginated automatically to avoid timeouts
- Circuit breaker protects against repository-specific failures
- Cache results are maintained for 365 days to avoid reprocessing

### Interruption Recovery

**Process Interrupted or Killed**
- Run the same command again - it will automatically resume
- State is saved after each repository completion
- Partial progress is preserved in `~/.hacktivity/state.db`
- Only pending repositories will be reprocessed

**Checking Operation Status**
```bash
# View state database for debugging
sqlite3 ~/.hacktivity/state.db "SELECT * FROM operations ORDER BY created_at DESC LIMIT 5;"

# Clear state if needed (forces full restart)
rm ~/.hacktivity/state.db
```

### Performance Optimization

**Memory Usage Concerns**
- Default configuration targets <500MB for large operations
- Reduce `max_workers` if memory usage is high
- Adjust `max_pages` to limit data per repository
- Clear old cache: `rm -rf ~/.hacktivity/cache/`

**Slow Processing**
- Enable parallel processing: `parallel_enabled = true`
- Increase workers: `max_workers = 8` (hardware permitting)
- Check network connectivity and GitHub API status
- Consider using organization/repository filters to reduce scope

**Circuit Breaker Triggered**
- Individual repository failures don't affect others
- Circuits automatically reset after cooldown period
- Check specific repository access permissions
- Review error logs for failing repository patterns

### Debug Information

**Verbose Logging**
```bash
# Enable debug logging
echo 'log_level = "DEBUG"' >> ~/.hacktivity/config.toml

# Or use environment variable
export LOG_LEVEL=DEBUG
hacktivity
```

**System Information**
```bash
# Check cache size and state
du -sh ~/.hacktivity/cache/
ls -la ~/.hacktivity/state.db

# View recent operations
sqlite3 ~/.hacktivity/state.db "SELECT operation_type, status, total_repositories, completed_repositories FROM operations ORDER BY created_at DESC LIMIT 10;"
```

### Cache Management

```bash
# Clear cache directory if needed
rm -rf ~/.hacktivity/cache/

# View cache and state size
du -sh ~/.hacktivity/cache/
du -sh ~/.hacktivity/state.db

# Reset all state (forces complete restart)
rm -rf ~/.hacktivity/cache/ ~/.hacktivity/state.db
```

## Development

### Running Tests

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with coverage
pytest --cov=hacktivity
```

### Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass
6. Submit a pull request

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/yourusername/hacktivity/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/hacktivity/discussions)

---

*Made with ❤️ for developers who want to track their progress*
