```python
# hacktivity/core/chunking.py

# ... (existing code) ...

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
```