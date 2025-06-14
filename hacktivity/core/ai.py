"""AI provider integration module."""

import os
import sys
import time
import hashlib
from typing import List, Dict, Tuple, Optional, Any

# Try to import the Google Generative AI library
try:
    import google.generativeai as genai
except ImportError:
    from .logging import get_logger
    logger = get_logger(__name__)
    logger.error("The 'google-generativeai' library is required. Please install it with 'pip install google-generativeai'")
    sys.exit(1)

from . import cache
from .logging import get_logger
from .config import get_config

logger = get_logger(__name__)


def check_ai_prerequisites() -> None:
    """Checks if the Gemini API key is set."""
    if not os.getenv("GEMINI_API_KEY"):
        logger.error("The GEMINI_API_KEY environment variable is not set.")
        logger.error("Please get a key from https://aistudio.google.com/app/apikey and set it.")
        logger.error("Example: export GEMINI_API_KEY='YOUR_API_KEY'")
        sys.exit(1)


def get_summary(commits: List[str], prompt: str) -> str:
    """
    Sends commit data to the Gemini API for summarization.
    
    Args:
        commits: List of commit messages
        prompt: The system prompt for summarization
        
    Returns:
        AI-generated summary text
    """
    if not commits:
        return "No commits found for the specified period. Nothing to summarize."

    logger.info("Sending data to Gemini for summarization...")

    # Configure the Gemini client
    config = get_config()
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(config.ai.model_name)

    # Join all commit messages into a single text block
    commit_text = "\n".join(f"- {msg}" for msg in commits)

    full_prompt = f"{prompt}\n\nHere is the raw commit data:\n\n---\n{commit_text}\n---"

    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        logger.error("An error occurred while communicating with the Gemini API: %s", e)
        sys.exit(1)


def _generate_batch_cache_key(commits_hash: str, prompt_hash: str, batch_index: int) -> str:
    """Generate a unique cache key for a batch of commits.
    
    Args:
        commits_hash: Hash of the commit messages in this batch
        prompt_hash: Hash of the prompt being used
        batch_index: Index of this batch in the overall processing
        
    Returns:
        Unique cache key string
    """
    return f"ai_batch:{commits_hash[:16]}:{prompt_hash[:8]}:{batch_index}"


def _hash_content(content: str) -> str:
    """Generate a consistent hash for content for caching purposes.
    
    Args:
        content: The content to hash
        
    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _split_commits_into_batches(commits: List[str], batch_size: int, overlap: int = 0) -> List[List[str]]:
    """Split commits into batches with optional overlap for context.
    
    Args:
        commits: List of commit messages
        batch_size: Number of commits per batch
        overlap: Number of commits to overlap between batches
        
    Returns:
        List of commit batches
    """
    if not commits:
        return []
    
    if len(commits) <= batch_size:
        return [commits]
    
    # Prevent memory explosion: overlap cannot be >= batch_size
    safe_overlap = min(overlap, batch_size - 1)
    if safe_overlap != overlap:
        logger.warning("Overlap (%d) >= batch_size (%d), clamping to %d to prevent memory issues", 
                      overlap, batch_size, safe_overlap)
    
    batches = []
    start = 0
    
    while start < len(commits):
        end = min(start + batch_size, len(commits))
        batch = commits[start:end]
        batches.append(batch)
        
        # Move start forward, accounting for safe overlap
        # Ensure we always make progress (at least 1 commit forward)
        step = max(1, batch_size - safe_overlap)
        start += step
        
        # Safety check: prevent infinite loops
        if start <= end - batch_size and len(batches) > len(commits):
            logger.error("Infinite loop detected in batch splitting, breaking")
            break
    
    logger.debug("Split %d commits into %d batches (size: %d, safe_overlap: %d)", 
                len(commits), len(batches), batch_size, safe_overlap)
    return batches


def get_batch_summary(commits: List[str], prompt: str, batch_index: int) -> str:
    """
    Process a single batch of commits with caching and error handling.
    
    Args:
        commits: List of commit messages for this batch
        prompt: The system prompt for summarization
        batch_index: Index of this batch (for cache key generation)
        
    Returns:
        AI-generated summary for this batch
    """
    if not commits:
        return ""
    
    # Generate cache key
    commits_text = "\n".join(commits)
    commits_hash = _hash_content(commits_text)
    prompt_hash = _hash_content(prompt)
    cache_key = _generate_batch_cache_key(commits_hash, prompt_hash, batch_index)
    
    # Check cache first
    cached_summary = cache.get(cache_key, max_age_hours=720)  # 30 days for batch summaries
    if cached_summary is not None:
        logger.debug("Using cached summary for batch %d (%d commits)", batch_index, len(commits))
        return cached_summary
    
    logger.info("Processing batch %d with %d commits", batch_index, len(commits))
    
    # Configure the Gemini client
    config = get_config()
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(config.ai.model_name)
    
    # Create batch-specific prompt
    commit_text = "\n".join(f"- {msg}" for msg in commits)
    batch_prompt = f"{prompt}\n\nBatch {batch_index + 1} commits:\n\n---\n{commit_text}\n---"
    
    try:
        response = model.generate_content(batch_prompt)
        summary = response.text
        
        # Cache the successful result
        cache.set(cache_key, summary)
        logger.debug("Cached summary for batch %d", batch_index)
        
        return summary
    except Exception as e:
        logger.error("Error processing batch %d: %s", batch_index, e)
        raise


def _aggregate_batch_summaries(batch_summaries: List[str], original_prompt: str) -> str:
    """
    Aggregate multiple batch summaries into a final comprehensive summary.
    
    Args:
        batch_summaries: List of summaries from individual batches
        original_prompt: The original prompt to maintain consistency
        
    Returns:
        Final aggregated summary
    """
    if not batch_summaries:
        return "No commits found for the specified period. Nothing to summarize."
    
    if len(batch_summaries) == 1:
        return batch_summaries[0]
    
    logger.info("Aggregating %d batch summaries into final summary", len(batch_summaries))
    
    # Configure the Gemini client
    config = get_config()
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(config.ai.model_name)
    
    # Create aggregation prompt
    summaries_text = "\n\n---\n\n".join(f"Summary {i+1}:\n{summary}" 
                                      for i, summary in enumerate(batch_summaries))
    
    aggregation_prompt = f"""
{original_prompt}

You are now reviewing multiple summaries that were created from batches of commits. Please create a comprehensive final summary that combines all the information below into a cohesive overview.

Individual batch summaries:

{summaries_text}

Please provide a unified summary that captures all the key themes, accomplishments, and patterns from across all batches.
"""
    
    try:
        response = model.generate_content(aggregation_prompt)
        return response.text
    except Exception as e:
        logger.error("Error aggregating batch summaries: %s", e)
        # Fallback: return concatenated summaries
        logger.warning("Falling back to concatenated summaries")
        return "\n\n".join(f"Batch {i+1} Summary:\n{summary}" 
                          for i, summary in enumerate(batch_summaries))


def get_batched_summary(commits: List[str], prompt: str) -> str:
    """
    Process commits in batches for improved efficiency and reliability.
    
    Args:
        commits: List of commit messages
        prompt: The system prompt for summarization
        
    Returns:
        AI-generated summary from batched processing
    """
    config = get_config()
    
    # Check if batching is enabled
    if not config.ai.batch_enabled:
        logger.debug("Batch processing disabled, using single-shot processing")
        return get_summary(commits, prompt)
    
    if len(commits) <= config.ai.batch_size:
        logger.debug("Commit count (%d) below batch threshold (%d), using single-shot processing", 
                    len(commits), config.ai.batch_size)
        return get_summary(commits, prompt)
    
    logger.info("Starting batch processing for %d commits (batch size: %d)", 
               len(commits), config.ai.batch_size)
    
    # Split commits into batches
    batches = _split_commits_into_batches(
        commits, 
        config.ai.batch_size, 
        config.ai.batch_overlap
    )
    
    # Process each batch with retry logic
    batch_summaries = []
    failed_batches = []
    
    for i, batch in enumerate(batches):
        retry_count = 0
        while retry_count <= config.ai.max_retries:
            try:
                summary = get_batch_summary(batch, prompt, i)
                batch_summaries.append(summary)
                logger.debug("Successfully processed batch %d/%d", i + 1, len(batches))
                break
            except Exception as e:
                retry_count += 1
                if retry_count <= config.ai.max_retries:
                    logger.warning("Batch %d failed (attempt %d/%d): %s. Retrying in %d seconds...", 
                                 i, retry_count, config.ai.max_retries, e, config.ai.retry_delay)
                    time.sleep(config.ai.retry_delay)
                else:
                    logger.error("Batch %d failed after %d attempts: %s", i, config.ai.max_retries, e)
                    failed_batches.append(i)
                    # Add placeholder to maintain batch order
                    batch_summaries.append(f"[Batch {i+1} processing failed after {config.ai.max_retries} attempts]")
    
    if failed_batches:
        logger.warning("Failed to process %d/%d batches: %s", 
                      len(failed_batches), len(batches), failed_batches)
    
    # Aggregate batch summaries
    final_summary = _aggregate_batch_summaries(batch_summaries, prompt)
    
    logger.info("Batch processing complete: %d batches processed, %d failed", 
               len(batches), len(failed_batches))
    
    return final_summary


def get_repository_summary(repo_name: str, commits: List[Dict[str, Any]], prompt: str) -> str:
    """
    Generate a summary for a single repository's commits with repository-specific caching.
    
    Args:
        repo_name: Name of the repository (e.g., 'owner/repo-name')
        commits: List of commit data for this repository
        prompt: The system prompt for summarization
        
    Returns:
        AI-generated summary for this repository
    """
    if not commits:
        return ""
    
    # Extract commit messages
    commit_messages = [commit.get('message', '') for commit in commits]
    if not commit_messages:
        return ""
    
    # Generate repository-specific cache key
    import hashlib
    commits_text = "\n".join(commit_messages)
    commits_hash = hashlib.sha256(commits_text.encode('utf-8')).hexdigest()
    prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    cache_key = f"repo_summary:{repo_name}:{commits_hash[:16]}:{prompt_hash[:8]}"
    
    # Check cache first
    from . import cache
    cached_summary = cache.get(cache_key, max_age_hours=720)  # 30 days for repository summaries
    if cached_summary is not None:
        logger.debug("Using cached summary for repository %s (%d commits)", repo_name, len(commit_messages))
        return cached_summary
    
    logger.info("Generating summary for repository %s (%d commits)", repo_name, len(commit_messages))
    
    # Create repository-specific prompt with context
    repo_prompt = f"{prompt}\n\nRepository: {repo_name}\nCommits in this repository:"
    
    # Generate summary using existing function
    summary = get_summary(commit_messages, repo_prompt)
    
    # Cache the result with repository-specific key
    cache.set(cache_key, summary)
    logger.debug("Cached summary for repository %s", repo_name)
    
    return summary


def get_repository_aware_summary(repo_commits: Dict[str, List[Dict[str, Any]]], prompt: str) -> str:
    """
    Process commits grouped by repository with repository-aware progress indicators.
    
    Args:
        repo_commits: Dictionary mapping repository names to commit data
        prompt: The system prompt for summarization
        
    Returns:
        AI-generated summary from repository-aware processing
    """
    if not repo_commits:
        return "No commits found for the specified period. Nothing to summarize."
    
    # Import Rich for progress display
    try:
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TaskID
        rich_available = True
    except ImportError:
        rich_available = False
        logger.warning("Rich not available, falling back to simple progress logging")
    
    # Prepare repository summaries
    repo_summaries = []
    total_repos = len(repo_commits)
    total_commits = sum(len(commits) for commits in repo_commits.values())
    
    logger.info("Processing %d repositories with %d total commits", total_repos, total_commits)
    
    if rich_available:
        # Use Rich progress bar for repository-aware progress
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total} repos"),
            transient=False  # Keep progress visible in non-debug mode
        ) as progress:
            task = progress.add_task("Processing repositories...", total=total_repos)
            
            for repo_name, commits in repo_commits.items():
                # Extract commit messages for this repository
                commit_messages = [commit.get('message', '') for commit in commits]
                
                # Update progress with current repository
                progress.update(task, description=f"Processing {repo_name} ({len(commit_messages)} commits)")
                
                # Generate summary for this repository
                if commits:
                    repo_summary = get_repository_summary(repo_name, commits, prompt)
                    if repo_summary:
                        repo_summaries.append(f"**{repo_name}** ({len(commit_messages)} commits):\n{repo_summary}")
                
                # Advance progress
                progress.update(task, advance=1)
                
                # Small delay to make progress visible
                time.sleep(0.1)
    else:
        # Fallback without Rich
        for i, (repo_name, commits) in enumerate(repo_commits.items(), 1):
            commit_messages = [commit.get('message', '') for commit in commits]
            logger.info("Processing repository %d/%d: %s (%d commits)", 
                       i, total_repos, repo_name, len(commit_messages))
            
            if commits:
                repo_summary = get_repository_summary(repo_name, commits, prompt)
                if repo_summary:
                    repo_summaries.append(f"**{repo_name}** ({len(commit_messages)} commits):\n{repo_summary}")
    
    # Aggregate repository summaries if we have multiple repositories
    if len(repo_summaries) == 1:
        return repo_summaries[0]
    elif len(repo_summaries) > 1:
        return _aggregate_repository_summaries(repo_summaries, prompt, repo_commits)
    else:
        return "No commits found for the specified period. Nothing to summarize."


def _aggregate_repository_summaries(repo_summaries: List[str], original_prompt: str, repo_commits: Dict[str, List[Dict[str, Any]]] = None) -> str:
    """
    Aggregate multiple repository summaries into a final comprehensive summary using AI.
    
    Args:
        repo_summaries: List of summaries from individual repositories
        original_prompt: The original prompt to maintain consistency
        repo_commits: Optional repository commit data for context
        
    Returns:
        Final aggregated summary organized by repository with AI-generated insights
    """
    if not repo_summaries:
        return "No commits found for the specified period. Nothing to summarize."
    
    if len(repo_summaries) == 1:
        return repo_summaries[0]
    
    # Calculate repository statistics for context
    repo_count = len(repo_summaries)
    total_commits = 0
    commit_distribution = []
    
    if repo_commits:
        for repo_name, commits in repo_commits.items():
            commit_count = len(commits)
            total_commits += commit_count
            commit_distribution.append(f"{repo_name}: {commit_count} commits")
    
    logger.info("Aggregating %d repository summaries into final summary (%d total commits)", 
               repo_count, total_commits)
    
    # Configure the Gemini client
    config = get_config()
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    model = genai.GenerativeModel(config.ai.model_name)
    
    # Create repository-aware aggregation prompt
    summaries_text = "\n\n---\n\n".join(f"Repository Summary {i+1} - {repo_summaries[i].split('**')[1].split('**')[0] if '**' in repo_summaries[i] else f'Repository {i+1}'}:\n{repo_summaries[i]}" 
                                        for i in range(len(repo_summaries)))
    
    # Build context information
    context_info = f"Total repositories: {repo_count}\nTotal commits: {total_commits}"
    if commit_distribution:
        context_info += f"\nCommit distribution:\n" + "\n".join(f"- {dist}" for dist in commit_distribution)
    
    aggregation_prompt = f"""{original_prompt}

You are reviewing multiple repository summaries for a comprehensive activity overview. Please create a well-structured final summary that:

1. Maintains repository organization and clarity
2. Identifies cross-repository patterns and themes
3. Highlights the most significant accomplishments
4. Provides actionable insights about the overall development activity

Context:
{context_info}

Repository Summaries:

{summaries_text}

Please provide a comprehensive summary that organizes the information clearly while identifying patterns and themes across repositories. Focus on the developer's key accomplishments and impact."""
    
    try:
        response = model.generate_content(aggregation_prompt)
        final_summary = response.text
        
        # Add header with repository context
        header = f"## Activity Summary ({repo_count} repositories, {total_commits} commits)\n\n"
        return header + final_summary
        
    except Exception as e:
        logger.error("Error aggregating repository summaries: %s", e)
        logger.warning("Falling back to structured concatenation")
        
        # Fallback: structured concatenation with context
        header = f"## Activity Summary Across {repo_count} Repositories ({total_commits} commits)\n\n"
        if commit_distribution:
            header += "### Repository Breakdown:\n" + "\n".join(f"- {dist}" for dist in commit_distribution) + "\n\n"
        
        organized_summary = "\n\n".join(repo_summaries)
        return header + organized_summary