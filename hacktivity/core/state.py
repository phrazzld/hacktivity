"""Operation state management module using SQLite."""

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

from .logging import get_logger

logger = get_logger(__name__)

# Lazy import config to avoid circular imports
def _get_config():
    from .config import get_config
    return get_config()


@dataclass
class Operation:
    """Represents a high-level operation (summary, fetch, etc.)."""
    id: str
    operation_type: str  # 'summary', 'fetch', etc.
    user: str
    since: str  # YYYY-MM-DD
    until: str  # YYYY-MM-DD
    author_filter: Optional[str] = None
    org_filter: Optional[str] = None
    repo_filter: Optional[str] = None
    status: str = 'pending'  # 'pending', 'in_progress', 'completed', 'failed', 'paused'
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    total_repositories: int = 0
    completed_repositories: int = 0
    total_commits: int = 0
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class RepositoryProgress:
    """Represents progress for a specific repository within an operation."""
    id: Optional[int] = None
    operation_id: str = ""
    repository_name: str = ""
    status: str = 'pending'  # 'pending', 'in_progress', 'completed', 'failed', 'skipped'
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    commit_count: int = 0
    chunk_count: int = 0
    completed_chunks: int = 0
    error_message: Optional[str] = None
    retry_count: int = 0


class StateManager:
    """SQLite-based state management for operations."""
    
    def __init__(self, db_path: Optional[str] = None):
        """Initialize the state manager.
        
        Args:
            db_path: Path to SQLite database. Defaults to ~/.hacktivity/state.db
        """
        if db_path is None:
            config = _get_config()
            state_dir = Path.home() / ".hacktivity"
            state_dir.mkdir(parents=True, exist_ok=True)
            db_path = state_dir / "state.db"
        
        self.db_path = Path(db_path)
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize the SQLite database with required tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    user TEXT NOT NULL,
                    since TEXT NOT NULL,
                    until TEXT NOT NULL,
                    author_filter TEXT,
                    org_filter TEXT,
                    repo_filter TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error_message TEXT,
                    total_repositories INTEGER,
                    completed_repositories INTEGER DEFAULT 0,
                    total_commits INTEGER DEFAULT 0,
                    metadata TEXT
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS repository_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL,
                    repository_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    commit_count INTEGER DEFAULT 0,
                    chunk_count INTEGER DEFAULT 0,
                    completed_chunks INTEGER DEFAULT 0,
                    error_message TEXT,
                    retry_count INTEGER DEFAULT 0,
                    FOREIGN KEY (operation_id) REFERENCES operations(id)
                )
            """)
            
            # Create indexes for better performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_status ON operations(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_operations_user ON operations(user)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_progress_operation ON repository_progress(operation_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_repo_progress_status ON repository_progress(operation_id, status)")
            
            conn.commit()
        
        logger.debug("Initialized state database at %s", self.db_path)
    
    def create_operation(
        self,
        operation_type: str,
        user: str,
        since: str,
        until: str,
        author_filter: Optional[str] = None,
        org_filter: Optional[str] = None,
        repo_filter: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """Create a new operation.
        
        Args:
            operation_type: Type of operation ('summary', 'fetch', etc.)
            user: GitHub username
            since: Start date in YYYY-MM-DD format
            until: End date in YYYY-MM-DD format
            author_filter: Optional author filter
            org_filter: Optional organization filter
            repo_filter: Optional repository filter
            metadata: Optional additional metadata
            
        Returns:
            Operation ID (UUID)
        """
        operation_id = str(uuid.uuid4())
        operation = Operation(
            id=operation_id,
            operation_type=operation_type,
            user=user,
            since=since,
            until=until,
            author_filter=author_filter,
            org_filter=org_filter,
            repo_filter=repo_filter,
            created_at=datetime.now().isoformat(),
            metadata=metadata
        )
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO operations (
                    id, operation_type, user, since, until, author_filter, org_filter, 
                    repo_filter, status, created_at, total_repositories, completed_repositories,
                    total_commits, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                operation.id,
                operation.operation_type,
                operation.user,
                operation.since,
                operation.until,
                operation.author_filter,
                operation.org_filter,
                operation.repo_filter,
                operation.status,
                operation.created_at,
                operation.total_repositories,
                operation.completed_repositories,
                operation.total_commits,
                json.dumps(operation.metadata) if operation.metadata else None
            ))
            conn.commit()
        
        logger.info("Created operation %s: %s for user %s (%s to %s)", 
                   operation_id, operation_type, user, since, until)
        return operation_id
    
    def get_operation(self, operation_id: str) -> Optional[Operation]:
        """Get operation by ID.
        
        Args:
            operation_id: Operation ID
            
        Returns:
            Operation object or None if not found
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM operations WHERE id = ?", (operation_id,))
            row = cursor.fetchone()
            
            if row is None:
                return None
            
            return Operation(
                id=row['id'],
                operation_type=row['operation_type'],
                user=row['user'],
                since=row['since'],
                until=row['until'],
                author_filter=row['author_filter'],
                org_filter=row['org_filter'],
                repo_filter=row['repo_filter'],
                status=row['status'],
                created_at=row['created_at'],
                started_at=row['started_at'],
                completed_at=row['completed_at'],
                error_message=row['error_message'],
                total_repositories=row['total_repositories'] or 0,
                completed_repositories=row['completed_repositories'] or 0,
                total_commits=row['total_commits'] or 0,
                metadata=json.loads(row['metadata']) if row['metadata'] else None
            )
    
    def update_operation_status(
        self,
        operation_id: str,
        status: str,
        error_message: Optional[str] = None,
        total_repositories: Optional[int] = None,
        total_commits: Optional[int] = None
    ) -> None:
        """Update operation status and metrics.
        
        Args:
            operation_id: Operation ID
            status: New status
            error_message: Optional error message
            total_repositories: Optional total repository count
            total_commits: Optional total commit count
        """
        updates = ["status = ?"]
        params = [status]
        
        # Set timestamp based on status
        if status == 'in_progress':
            updates.append("started_at = ?")
            params.append(datetime.now().isoformat())
        elif status in ['completed', 'failed']:
            updates.append("completed_at = ?")
            params.append(datetime.now().isoformat())
        
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        
        if total_repositories is not None:
            updates.append("total_repositories = ?")
            params.append(total_repositories)
        
        if total_commits is not None:
            updates.append("total_commits = ?")
            params.append(total_commits)
        
        params.append(operation_id)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE operations SET {', '.join(updates)} WHERE id = ?", params)
            conn.commit()
        
        logger.debug("Updated operation %s status to %s", operation_id, status)
    
    def add_repositories_to_operation(self, operation_id: str, repositories: List[str]) -> None:
        """Add repositories to track for an operation.
        
        Args:
            operation_id: Operation ID
            repositories: List of repository names (owner/repo format)
        """
        with sqlite3.connect(self.db_path) as conn:
            for repo_name in repositories:
                conn.execute("""
                    INSERT INTO repository_progress (
                        operation_id, repository_name, status
                    ) VALUES (?, ?, ?)
                """, (operation_id, repo_name, 'pending'))
            
            # Update total repository count
            conn.execute("""
                UPDATE operations SET total_repositories = ? WHERE id = ?
            """, (len(repositories), operation_id))
            
            conn.commit()
        
        logger.info("Added %d repositories to operation %s", len(repositories), operation_id)
    
    def update_repository_progress(
        self,
        operation_id: str,
        repository_name: str,
        status: str,
        commit_count: Optional[int] = None,
        chunk_count: Optional[int] = None,
        completed_chunks: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Update progress for a specific repository.
        
        Args:
            operation_id: Operation ID
            repository_name: Repository name
            status: New status
            commit_count: Number of commits found
            chunk_count: Total number of chunks
            completed_chunks: Number of completed chunks
            error_message: Optional error message
        """
        updates = ["status = ?"]
        params = [status]
        
        # Set timestamp based on status
        if status == 'in_progress':
            updates.append("started_at = ?")
            params.append(datetime.now().isoformat())
        elif status in ['completed', 'failed', 'skipped']:
            updates.append("completed_at = ?")
            params.append(datetime.now().isoformat())
        
        if commit_count is not None:
            updates.append("commit_count = ?")
            params.append(commit_count)
        
        if chunk_count is not None:
            updates.append("chunk_count = ?")
            params.append(chunk_count)
        
        if completed_chunks is not None:
            updates.append("completed_chunks = ?")
            params.append(completed_chunks)
        
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
            # Increment retry count on error
            updates.append("retry_count = retry_count + 1")
        
        params.extend([operation_id, repository_name])
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"""
                UPDATE repository_progress 
                SET {', '.join(updates)} 
                WHERE operation_id = ? AND repository_name = ?
            """, params)
            
            # Update operation-level completed repository count
            if status in ['completed', 'skipped']:
                conn.execute("""
                    UPDATE operations 
                    SET completed_repositories = (
                        SELECT COUNT(*) FROM repository_progress 
                        WHERE operation_id = ? AND status IN ('completed', 'skipped')
                    )
                    WHERE id = ?
                """, (operation_id, operation_id))
            
            # Update total commit count
            if commit_count is not None:
                conn.execute("""
                    UPDATE operations 
                    SET total_commits = (
                        SELECT COALESCE(SUM(commit_count), 0) FROM repository_progress 
                        WHERE operation_id = ?
                    )
                    WHERE id = ?
                """, (operation_id, operation_id))
            
            conn.commit()
        
        logger.debug("Updated repository %s progress: %s", repository_name, status)
    
    def get_pending_repositories(self, operation_id: str) -> List[str]:
        """Get list of repositories that still need processing.
        
        Args:
            operation_id: Operation ID
            
        Returns:
            List of repository names that are pending, in_progress, or failed (for retry/resume)
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT repository_name FROM repository_progress 
                WHERE operation_id = ? AND status IN ('pending', 'in_progress', 'failed')
                ORDER BY repository_name
            """, (operation_id,))
            
            return [row[0] for row in cursor.fetchall()]
    
    def get_operation_summary(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive operation status summary.
        
        Args:
            operation_id: Operation ID
            
        Returns:
            Dictionary with operation status and progress details
        """
        operation = self.get_operation(operation_id)
        if operation is None:
            return None
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get repository status counts
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM repository_progress 
                WHERE operation_id = ?
                GROUP BY status
            """, (operation_id,))
            
            repo_status = {row['status']: row['count'] for row in cursor.fetchall()}
            
            # Calculate progress percentage
            total_repos = operation.total_repositories
            completed_repos = operation.completed_repositories
            progress_pct = (completed_repos / total_repos * 100) if total_repos > 0 else 0
            
            return {
                'operation': asdict(operation),
                'repository_status': repo_status,
                'progress_percentage': progress_pct,
                'repositories_pending': repo_status.get('pending', 0),
                'repositories_in_progress': repo_status.get('in_progress', 0),
                'repositories_completed': repo_status.get('completed', 0),
                'repositories_failed': repo_status.get('failed', 0),
                'repositories_skipped': repo_status.get('skipped', 0)
            }
    
    def list_recent_operations(self, limit: int = 10, user: Optional[str] = None) -> List[Operation]:
        """List recent operations.
        
        Args:
            limit: Maximum number of operations to return
            user: Optional user filter
            
        Returns:
            List of operations, newest first
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT * FROM operations"
            params = []
            
            if user is not None:
                query += " WHERE user = ?"
                params.append(user)
            
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            
            operations = []
            for row in cursor.fetchall():
                operations.append(Operation(
                    id=row['id'],
                    operation_type=row['operation_type'],
                    user=row['user'],
                    since=row['since'],
                    until=row['until'],
                    author_filter=row['author_filter'],
                    org_filter=row['org_filter'],
                    repo_filter=row['repo_filter'],
                    status=row['status'],
                    created_at=row['created_at'],
                    started_at=row['started_at'],
                    completed_at=row['completed_at'],
                    error_message=row['error_message'],
                    total_repositories=row['total_repositories'] or 0,
                    completed_repositories=row['completed_repositories'] or 0,
                    total_commits=row['total_commits'] or 0,
                    metadata=json.loads(row['metadata']) if row['metadata'] else None
                ))
            
            return operations
    
    def cleanup_old_operations(self, days: int = 30) -> int:
        """Clean up old completed operations.
        
        Args:
            days: Delete operations older than this many days
            
        Returns:
            Number of operations deleted
        """
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)
        cutoff_iso = cutoff_date.isoformat()
        
        with sqlite3.connect(self.db_path) as conn:
            # Delete repository progress first (foreign key constraint)
            conn.execute("""
                DELETE FROM repository_progress 
                WHERE operation_id IN (
                    SELECT id FROM operations 
                    WHERE status IN ('completed', 'failed') AND created_at < ?
                )
            """, (cutoff_iso,))
            
            # Delete operations
            cursor = conn.execute("""
                DELETE FROM operations 
                WHERE status IN ('completed', 'failed') AND created_at < ?
            """, (cutoff_iso,))
            
            deleted_count = cursor.rowcount
            conn.commit()
        
        logger.info("Cleaned up %d old operations (older than %d days)", deleted_count, days)
        return deleted_count


# Global state manager instance
_state_manager = None


def get_state_manager() -> StateManager:
    """Get the global state manager instance."""
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager


def create_operation(
    operation_type: str,
    user: str,
    since: str,
    until: str,
    author_filter: Optional[str] = None,
    org_filter: Optional[str] = None,
    repo_filter: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> str:
    """Create a new operation. Convenience function."""
    return get_state_manager().create_operation(
        operation_type, user, since, until, author_filter, org_filter, repo_filter, metadata
    )


def get_operation_status(operation_id: str) -> Optional[Dict[str, Any]]:
    """Get operation status summary. Convenience function."""
    return get_state_manager().get_operation_summary(operation_id)


def update_operation_status(operation_id: str, status: str, **kwargs) -> None:
    """Update operation status. Convenience function."""
    return get_state_manager().update_operation_status(operation_id, status, **kwargs)


def track_repository_progress(operation_id: str, repository_name: str, status: str, **kwargs) -> None:
    """Track repository progress. Convenience function."""
    return get_state_manager().update_repository_progress(operation_id, repository_name, status, **kwargs)


def get_pending_repositories(operation_id: str) -> List[str]:
    """Get pending repositories. Convenience function."""
    return get_state_manager().get_pending_repositories(operation_id)