"""Date range chunking module for processing large time periods."""

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any

from . import cache
from .logging import get_logger

logger = get_logger(__name__)

# Import commits module for processing individual chunks
def _get_fetch_function():
    """Lazy import to avoid circular imports."""
    from .commits import fetch_repo_commits
    return fetch_repo_commits


@dataclass
class DateChunk:
    """Represents a single date range chunk."""
    since: str  # YYYY-MM-DD format
    until: str  # YYYY-MM-DD format
    index: int  # Chunk index in the sequence
    
    def __str__(self) -> str:
        return f"DateChunk({self.index}: {self.since} to {self.until})"


@dataclass
class ChunkState:
    """Represents the processing state of a chunk."""
    chunk_index: int
    status: str  # 'pending', 'in_progress', 'completed', 'failed'
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    commit_count: int = 0
    error_message: Optional[str] = None


def create_date_chunks(since: str, until: str, max_days: int = 7) -> List[DateChunk]:
    """
    Create date chunks from a date range.
    
    Args:
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        max_days: Maximum days per chunk (default: 7 for weekly chunks)
        
    Returns:
        List of DateChunk objects
        
    Raises:
        ValueError: If since > until or invalid date format
    """
    try:
        start_date = datetime.strptime(since, '%Y-%m-%d')
        end_date = datetime.strptime(until, '%Y-%m-%d')
    except ValueError as e:
        raise ValueError(f"Invalid date format. Expected YYYY-MM-DD: {e}")
    
    if start_date > end_date:
        raise ValueError(f"Start date ({since}) must be before or equal to end date ({until})")
    
    chunks = []
    current_date = start_date
    chunk_index = 0
    
    while current_date <= end_date:
        # Calculate chunk end date (either max_days later or the final end_date)
        chunk_end = min(current_date + timedelta(days=max_days - 1), end_date)
        
        chunk = DateChunk(
            since=current_date.strftime('%Y-%m-%d'),
            until=chunk_end.strftime('%Y-%m-%d'),
            index=chunk_index
        )
        chunks.append(chunk)
        
        # Move to next chunk
        current_date = chunk_end + timedelta(days=1)
        chunk_index += 1
    
    logger.info("Created %d chunks from %s to %s (max %d days each)", 
               len(chunks), since, until, max_days)
    return chunks


def get_chunk_state_key(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> str:
    """Generate a unique cache key for chunk state tracking.
    
    Args:
        repo_full_name: Repository full name (owner/repo)
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional author username filter
        
    Returns:
        Unique cache key string for chunk state
    """
    author_part = author_filter or "all"
    return f"chunk_state:{repo_full_name}:{since}:{until}:{author_part}"


def aggregate_chunk_results(chunk_results: Dict[int, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Aggregate commit results from multiple chunks.
    
    Args:
        chunk_results: Dictionary mapping chunk indices to their commit lists
        
    Returns:
        Flat list of all commits, sorted by commit_date (newest first)
    """
    all_commits = []
    
    # Collect all commits from all chunks
    for chunk_index in sorted(chunk_results.keys()):
        commits = chunk_results[chunk_index]
        for commit in commits:
            # Add chunk information for debugging
            commit_with_chunk = commit.copy()
            commit_with_chunk['_chunk_index'] = chunk_index
            all_commits.append(commit_with_chunk)
    
    # Sort by commit_date (newest first)
    # Handle missing or invalid dates gracefully
    def get_sort_key(commit):
        commit_date = commit.get('commit_date', '')
        try:
            return datetime.fromisoformat(commit_date.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            # Invalid or missing dates go to the end (use epoch with timezone)
            from datetime import timezone
            return datetime.fromtimestamp(0, tz=timezone.utc)
    
    all_commits.sort(key=get_sort_key, reverse=True)
    
    logger.info("Aggregated %d commits from %d chunks", len(all_commits), len(chunk_results))
    return all_commits


def save_chunk_state(state_key: str, chunk_states: Dict[int, ChunkState], chunk_results: Dict[int, List[Dict[str, Any]]]) -> None:
    """
    Save chunk processing state to cache.
    
    Args:
        state_key: Cache key for the state
        chunk_states: Dictionary mapping chunk indices to their states
        chunk_results: Dictionary mapping chunk indices to their results
    """
    state_data = {
        'chunks': {str(k): asdict(v) for k, v in chunk_states.items()},
        'chunk_results': {str(k): v for k, v in chunk_results.items()},
        'last_updated': datetime.now().isoformat()
    }
    
    # Use longer TTL for chunk state (30 days) since it tracks progress
    cache.get_cache().set(state_key, state_data)
    logger.debug("Saved chunk state for %d chunks", len(chunk_states))


def load_chunk_state(state_key: str) -> tuple[Dict[int, ChunkState], Dict[int, List[Dict[str, Any]]]]:
    """
    Load chunk processing state from cache.
    
    Args:
        state_key: Cache key for the state
        
    Returns:
        Tuple of (chunk_states, chunk_results)
    """
    # Use 30-day TTL for chunk state
    state_data = cache.get(state_key, max_age_hours=720)  # 30 days
    
    if state_data is None:
        return {}, {}
    
    # Reconstruct chunk states
    chunk_states = {}
    for k, v in state_data.get('chunks', {}).items():
        chunk_states[int(k)] = ChunkState(**v)
    
    # Reconstruct chunk results
    chunk_results = {}
    for k, v in state_data.get('chunk_results', {}).items():
        chunk_results[int(k)] = v
    
    logger.info("Loaded chunk state for %d chunks", len(chunk_states))
    return chunk_states, chunk_results


def process_chunks_with_state(
    repo_full_name: str, 
    since: str, 
    until: str, 
    author_filter: Optional[str], 
    chunks: List[DateChunk]
) -> List[Dict[str, Any]]:
    """
    Process date chunks with state management for resumability.
    
    Args:
        repo_full_name: Repository full name (e.g., 'owner/repo-name')
        since: Start date in YYYY-MM-DD format (for state key)
        until: End date in YYYY-MM-DD format (for state key)
        author_filter: Optional GitHub username to filter commits by
        chunks: List of DateChunk objects to process
        
    Returns:
        Aggregated list of commits from all successful chunks
    """
    fetch_repo_commits = _get_fetch_function()
    
    # Load existing state
    state_key = get_chunk_state_key(repo_full_name, since, until, author_filter)
    chunk_states, chunk_results = load_chunk_state(state_key)
    
    logger.info("Processing %d chunks for %s (%s to %s)", 
               len(chunks), repo_full_name, since, until)
    
    # Process each chunk
    for chunk in chunks:
        chunk_index = chunk.index
        
        # Skip if chunk is already completed
        if chunk_index in chunk_states and chunk_states[chunk_index].status == 'completed':
            logger.info("Skipping chunk %d (already completed): %s", chunk_index, chunk)
            continue
        
        # Mark chunk as in progress
        chunk_states[chunk_index] = ChunkState(
            chunk_index=chunk_index,
            status='in_progress',
            start_time=datetime.now().isoformat()
        )
        
        # Save state before processing
        save_chunk_state(state_key, chunk_states, chunk_results)
        
        try:
            logger.info("Processing chunk %d: %s", chunk_index, chunk)
            
            # Fetch commits for this chunk
            commits = fetch_repo_commits(repo_full_name, chunk.since, chunk.until, author_filter)
            
            # Store results
            chunk_results[chunk_index] = commits
            
            # Mark chunk as completed
            chunk_states[chunk_index].status = 'completed'
            chunk_states[chunk_index].end_time = datetime.now().isoformat()
            chunk_states[chunk_index].commit_count = len(commits)
            
            logger.info("Completed chunk %d: %d commits", chunk_index, len(commits))
            
        except Exception as e:
            # Mark chunk as failed
            chunk_states[chunk_index].status = 'failed'
            chunk_states[chunk_index].end_time = datetime.now().isoformat()
            chunk_states[chunk_index].error_message = str(e)
            
            logger.error("Failed to process chunk %d: %s", chunk_index, e)
            
            # Continue with other chunks instead of failing entirely
            
        finally:
            # Save state after each chunk
            save_chunk_state(state_key, chunk_states, chunk_results)
    
    # Aggregate results from all successful chunks
    aggregated_results = aggregate_chunk_results(chunk_results)
    
    # Log summary
    completed_chunks = sum(1 for state in chunk_states.values() if state.status == 'completed')
    failed_chunks = sum(1 for state in chunk_states.values() if state.status == 'failed')
    total_commits = len(aggregated_results)
    
    logger.info("Chunk processing complete: %d completed, %d failed, %d total commits", 
               completed_chunks, failed_chunks, total_commits)
    
    return aggregated_results


def fetch_repo_commits_chunked(
    repo_full_name: str, 
    since: str, 
    until: str, 
    author_filter: Optional[str] = None,
    max_days: int = 7,
    operation_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Fetch repository commits using date range chunking for robustness.
    
    This is the main interface for chunked commit fetching. It automatically
    creates chunks, manages state, and returns aggregated results.
    
    Args:
        repo_full_name: Repository full name (e.g., 'owner/repo-name')
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional GitHub username to filter commits by
        max_days: Maximum days per chunk (default: 7 for weekly chunks)
        operation_id: Optional operation ID for tracking progress at operation level
        
    Returns:
        List of commit dictionaries, sorted by commit_date (newest first)
    """
    # Track repository progress at operation level if operation_id provided
    if operation_id:
        try:
            from .state import track_repository_progress
            track_repository_progress(operation_id, repo_full_name, 'in_progress')
        except ImportError:
            logger.warning("State management not available, continuing without operation tracking")
    
    try:
        # Create chunks
        chunks = create_date_chunks(since, until, max_days)
        
        # Update chunk count in operation state if available
        if operation_id:
            try:
                track_repository_progress(operation_id, repo_full_name, 'in_progress', chunk_count=len(chunks))
            except (ImportError, NameError):
                pass
        
        if len(chunks) == 1:
            # For single chunk, use direct fetch (no need for state management overhead)
            logger.info("Single chunk detected, using direct fetch")
            fetch_repo_commits = _get_fetch_function()
            result = fetch_repo_commits(repo_full_name, since, until, author_filter)
        else:
            # Process chunks with state management
            result = process_chunks_with_state(repo_full_name, since, until, author_filter, chunks)
        
        # Update repository completion status
        if operation_id:
            try:
                track_repository_progress(
                    operation_id, 
                    repo_full_name, 
                    'completed',
                    commit_count=len(result),
                    completed_chunks=len(chunks)
                )
            except (ImportError, NameError):
                pass
        
        return result
        
    except Exception as e:
        # Track failure in operation state if available
        if operation_id:
            try:
                track_repository_progress(
                    operation_id, 
                    repo_full_name, 
                    'failed',
                    error_message=str(e)
                )
            except (ImportError, NameError):
                pass
        raise


def get_chunked_progress(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> Dict[str, Any]:
    """
    Get progress information for a chunked operation.
    
    Args:
        repo_full_name: Repository full name
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional author filter
        
    Returns:
        Dictionary with progress information
    """
    state_key = get_chunk_state_key(repo_full_name, since, until, author_filter)
    chunk_states, chunk_results = load_chunk_state(state_key)
    
    if not chunk_states:
        return {
            'status': 'not_started',
            'total_chunks': 0,
            'completed_chunks': 0,
            'failed_chunks': 0,
            'total_commits': 0
        }
    
    completed_chunks = sum(1 for state in chunk_states.values() if state.status == 'completed')
    failed_chunks = sum(1 for state in chunk_states.values() if state.status == 'failed')
    total_commits = sum(len(results) for results in chunk_results.values())
    
    # Determine overall status
    if completed_chunks == len(chunk_states):
        overall_status = 'completed'
    elif failed_chunks == len(chunk_states):
        overall_status = 'failed'
    elif completed_chunks + failed_chunks == len(chunk_states):
        overall_status = 'completed_with_errors'
    else:
        overall_status = 'in_progress'
    
    return {
        'status': overall_status,
        'total_chunks': len(chunk_states),
        'completed_chunks': completed_chunks,
        'failed_chunks': failed_chunks,
        'total_commits': total_commits,
        'progress_percentage': (completed_chunks / len(chunk_states)) * 100 if chunk_states else 0
    }


def retry_failed_chunks(repo_full_name: str, since: str, until: str, author_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Retry processing of failed chunks.
    
    Args:
        repo_full_name: Repository full name
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional author filter
        
    Returns:
        Aggregated results after retrying failed chunks
    """
    state_key = get_chunk_state_key(repo_full_name, since, until, author_filter)
    chunk_states, chunk_results = load_chunk_state(state_key)
    
    if not chunk_states:
        logger.warning("No chunk state found for retry operation")
        return []
    
    # Find failed chunks and reset their status
    failed_chunk_indices = []
    for chunk_index, state in chunk_states.items():
        if state.status == 'failed':
            state.status = 'pending'
            state.error_message = None
            failed_chunk_indices.append(chunk_index)
    
    if not failed_chunk_indices:
        logger.info("No failed chunks to retry")
        return aggregate_chunk_results(chunk_results)
    
    logger.info("Retrying %d failed chunks", len(failed_chunk_indices))
    
    # Recreate chunks for failed indices (we need the date info)
    # This is a bit of a workaround - in a real implementation we might store
    # the chunk definitions in the state as well
    all_chunks = create_date_chunks(since, until)
    failed_chunks = [chunk for chunk in all_chunks if chunk.index in failed_chunk_indices]
    
    # Process only the failed chunks
    return process_chunks_with_state(repo_full_name, since, until, author_filter, failed_chunks)


def process_repositories_with_operation_state(
    operation_id: str,
    repositories: List[str],
    since: str,
    until: str,
    author_filter: Optional[str] = None,
    max_days: int = 7
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Process multiple repositories with full operation-level state tracking.
    
    This function provides the complete integration between repository processing,
    chunking, and operation state management. It enables resumable operations
    across multiple repositories.
    
    Args:
        operation_id: Operation ID for state tracking
        repositories: List of repository names to process
        since: Start date in YYYY-MM-DD format
        until: End date in YYYY-MM-DD format
        author_filter: Optional GitHub username to filter commits by
        max_days: Maximum days per chunk (default: 7 for weekly chunks)
        
    Returns:
        Dictionary mapping repository names to their commit lists
    """
    from .state import get_state_manager, get_pending_repositories
    
    state_manager = get_state_manager()
    all_results = {}
    
    # Add repositories to operation if not already added
    try:
        state_manager.add_repositories_to_operation(operation_id, repositories)
    except Exception as e:
        # May already be added, check if operation exists
        operation = state_manager.get_operation(operation_id)
        if operation is None:
            raise ValueError(f"Operation {operation_id} not found") from e
        # Otherwise, repositories likely already added, continue
    
    # Update operation status
    state_manager.update_operation_status(operation_id, 'in_progress')
    
    # Get repositories that still need processing (enables resume)
    pending_repos = get_pending_repositories(operation_id)
    repos_to_process = [repo for repo in repositories if repo in pending_repos]
    
    if not repos_to_process:
        logger.info("No pending repositories for operation %s", operation_id)
        # Still return results from completed repositories
        # TODO: Could load from state if needed
        return all_results
    
    logger.info("Processing %d repositories for operation %s (resume from %d pending)", 
               len(repos_to_process), operation_id, len(repos_to_process))
    
    completed_count = 0
    failed_count = 0
    
    for repo_name in repos_to_process:
        try:
            logger.info("Processing repository %s (%d/%d)", 
                       repo_name, completed_count + failed_count + 1, len(repos_to_process))
            
            # Process repository with chunking and state tracking
            commits = fetch_repo_commits_chunked(
                repo_name, 
                since, 
                until, 
                author_filter, 
                max_days,
                operation_id
            )
            
            all_results[repo_name] = commits
            completed_count += 1
            
            logger.info("Completed repository %s: %d commits", repo_name, len(commits))
            
        except Exception as e:
            logger.error("Failed to process repository %s: %s", repo_name, e)
            all_results[repo_name] = []
            failed_count += 1
            
            # Error already tracked in fetch_repo_commits_chunked
            continue
    
    # Update final operation status
    if failed_count == 0:
        state_manager.update_operation_status(operation_id, 'completed')
        logger.info("Operation %s completed successfully: %d repositories processed", 
                   operation_id, completed_count)
    else:
        # Partial success - some repos failed
        if completed_count > 0:
            logger.warning("Operation %s completed with errors: %d succeeded, %d failed", 
                          operation_id, completed_count, failed_count)
        else:
            state_manager.update_operation_status(
                operation_id, 
                'failed', 
                error_message=f"All {failed_count} repositories failed"
            )
            logger.error("Operation %s failed: all repositories failed", operation_id)
    
    return all_results