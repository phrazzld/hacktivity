"""Unit tests for state management module."""

import json
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from hacktivity.core.state import (
    StateManager, Operation, RepositoryProgress,
    create_operation, get_operation_status, update_operation_status,
    track_repository_progress, get_pending_repositories
)


class TestOperation:
    """Test the Operation dataclass."""
    
    def test_operation_creation(self):
        """Test creating an Operation."""
        operation = Operation(
            id="test-id",
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        assert operation.id == "test-id"
        assert operation.operation_type == "summary"
        assert operation.user == "testuser"
        assert operation.status == "pending"  # default
        assert operation.total_repositories == 0  # default
        
    def test_operation_with_filters(self):
        """Test Operation with optional filters."""
        operation = Operation(
            id="test-id",
            operation_type="fetch",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31",
            author_filter="author1",
            org_filter="myorg",
            repo_filter="myorg/repo"
        )
        assert operation.author_filter == "author1"
        assert operation.org_filter == "myorg"
        assert operation.repo_filter == "myorg/repo"


class TestRepositoryProgress:
    """Test the RepositoryProgress dataclass."""
    
    def test_repository_progress_creation(self):
        """Test creating a RepositoryProgress."""
        progress = RepositoryProgress(
            operation_id="op-123",
            repository_name="owner/repo",
            status="completed",
            commit_count=42
        )
        assert progress.operation_id == "op-123"
        assert progress.repository_name == "owner/repo"
        assert progress.status == "completed"
        assert progress.commit_count == 42
        assert progress.retry_count == 0  # default


class TestStateManager:
    """Test the StateManager class."""
    
    def setup_method(self):
        """Set up test environment with temporary database."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_state.db"
        self.state_manager = StateManager(str(self.db_path))
    
    def teardown_method(self):
        """Clean up test environment."""
        if self.db_path.exists():
            self.db_path.unlink()
    
    def test_database_initialization(self):
        """Test that database is properly initialized."""
        assert self.db_path.exists()
        
        # Check that tables were created
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            
        assert "operations" in tables
        assert "repository_progress" in tables
        
        # Check that indexes were created
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = {row[0] for row in cursor.fetchall()}
            
        assert "idx_operations_status" in indexes
        assert "idx_operations_user" in indexes
        assert "idx_repo_progress_operation" in indexes
    
    def test_create_operation(self):
        """Test creating a new operation."""
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31",
            author_filter="author1",
            metadata={"test": "data"}
        )
        
        # Should return a UUID
        assert len(operation_id) == 36  # UUID format
        assert "-" in operation_id
        
        # Check that operation was stored
        operation = self.state_manager.get_operation(operation_id)
        assert operation is not None
        assert operation.operation_type == "summary"
        assert operation.user == "testuser"
        assert operation.since == "2024-01-01"
        assert operation.until == "2024-01-31"
        assert operation.author_filter == "author1"
        assert operation.status == "pending"
        assert operation.metadata == {"test": "data"}
        assert operation.created_at is not None
    
    def test_get_nonexistent_operation(self):
        """Test getting an operation that doesn't exist."""
        operation = self.state_manager.get_operation("nonexistent-id")
        assert operation is None
    
    def test_update_operation_status(self):
        """Test updating operation status."""
        # Create operation
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        
        # Update to in_progress
        self.state_manager.update_operation_status(
            operation_id, 
            "in_progress", 
            total_repositories=5
        )
        
        operation = self.state_manager.get_operation(operation_id)
        assert operation.status == "in_progress"
        assert operation.started_at is not None
        assert operation.total_repositories == 5
        
        # Update to completed with error
        self.state_manager.update_operation_status(
            operation_id,
            "failed",
            error_message="Test error",
            total_commits=100
        )
        
        operation = self.state_manager.get_operation(operation_id)
        assert operation.status == "failed"
        assert operation.completed_at is not None
        assert operation.error_message == "Test error"
        assert operation.total_commits == 100
    
    def test_add_repositories_to_operation(self):
        """Test adding repositories to track for an operation."""
        # Create operation
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        
        # Add repositories
        repositories = ["owner/repo1", "owner/repo2", "org/repo3"]
        self.state_manager.add_repositories_to_operation(operation_id, repositories)
        
        # Check that operation was updated
        operation = self.state_manager.get_operation(operation_id)
        assert operation.total_repositories == 3
        
        # Check that repository progress records were created
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT repository_name, status FROM repository_progress WHERE operation_id = ?",
                (operation_id,)
            )
            repo_data = cursor.fetchall()
        
        assert len(repo_data) == 3
        repo_names = {row[0] for row in repo_data}
        assert repo_names == set(repositories)
        
        # All should be pending initially
        statuses = {row[1] for row in repo_data}
        assert statuses == {"pending"}
    
    def test_update_repository_progress(self):
        """Test updating repository progress."""
        # Create operation with repositories
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        repositories = ["owner/repo1", "owner/repo2"]
        self.state_manager.add_repositories_to_operation(operation_id, repositories)
        
        # Update first repository to in_progress
        self.state_manager.update_repository_progress(
            operation_id,
            "owner/repo1",
            "in_progress",
            chunk_count=5
        )
        
        # Update first repository to completed
        self.state_manager.update_repository_progress(
            operation_id,
            "owner/repo1",
            "completed",
            commit_count=42,
            completed_chunks=5
        )
        
        # Update second repository to failed
        self.state_manager.update_repository_progress(
            operation_id,
            "owner/repo2",
            "failed",
            error_message="API error"
        )
        
        # Check repository progress
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT repository_name, status, commit_count, completed_chunks, error_message, retry_count
                FROM repository_progress WHERE operation_id = ? ORDER BY repository_name
            """, (operation_id,))
            
            rows = cursor.fetchall()
        
        assert len(rows) == 2
        
        # First repo should be completed
        repo1 = rows[0]
        assert repo1['repository_name'] == "owner/repo1"
        assert repo1['status'] == "completed"
        assert repo1['commit_count'] == 42
        assert repo1['completed_chunks'] == 5
        
        # Second repo should be failed
        repo2 = rows[1]
        assert repo2['repository_name'] == "owner/repo2"
        assert repo2['status'] == "failed"
        assert repo2['error_message'] == "API error"
        assert repo2['retry_count'] == 1  # Should increment on error
        
        # Check that operation completed_repositories was updated
        operation = self.state_manager.get_operation(operation_id)
        assert operation.completed_repositories == 1  # Only completed ones count
        assert operation.total_commits == 42  # Sum of commit counts
    
    def test_get_pending_repositories(self):
        """Test getting list of pending repositories."""
        # Create operation with repositories
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        repositories = ["owner/repo1", "owner/repo2", "owner/repo3", "owner/repo4"]
        self.state_manager.add_repositories_to_operation(operation_id, repositories)
        
        # Update some repositories
        self.state_manager.update_repository_progress(operation_id, "owner/repo1", "completed")
        self.state_manager.update_repository_progress(operation_id, "owner/repo2", "failed")
        self.state_manager.update_repository_progress(operation_id, "owner/repo3", "in_progress")
        # owner/repo4 remains pending
        
        pending = self.state_manager.get_pending_repositories(operation_id)
        
        # Should return pending, in_progress, and failed repositories (for retry/resume)
        assert set(pending) == {"owner/repo2", "owner/repo3", "owner/repo4"}
        assert pending == sorted(pending)  # Should be sorted
    
    def test_get_operation_summary(self):
        """Test getting comprehensive operation summary."""
        # Create operation with repositories
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        repositories = ["owner/repo1", "owner/repo2", "owner/repo3", "owner/repo4"]
        self.state_manager.add_repositories_to_operation(operation_id, repositories)
        
        # Update repositories to different statuses
        self.state_manager.update_repository_progress(operation_id, "owner/repo1", "completed", commit_count=10)
        self.state_manager.update_repository_progress(operation_id, "owner/repo2", "completed", commit_count=20)
        self.state_manager.update_repository_progress(operation_id, "owner/repo3", "failed")
        # owner/repo4 remains pending
        
        summary = self.state_manager.get_operation_summary(operation_id)
        
        assert summary is not None
        assert summary['operation']['id'] == operation_id
        assert summary['operation']['total_repositories'] == 4
        assert summary['operation']['completed_repositories'] == 2
        assert summary['operation']['total_commits'] == 30
        
        assert summary['repository_status']['pending'] == 1
        assert summary['repository_status']['completed'] == 2
        assert summary['repository_status']['failed'] == 1
        assert summary['repository_status'].get('in_progress', 0) == 0
        
        assert summary['progress_percentage'] == 50.0  # 2/4 completed
        assert summary['repositories_completed'] == 2
        assert summary['repositories_failed'] == 1
        assert summary['repositories_pending'] == 1
    
    def test_get_operation_summary_nonexistent(self):
        """Test getting summary for nonexistent operation."""
        summary = self.state_manager.get_operation_summary("nonexistent-id")
        assert summary is None
    
    def test_list_recent_operations(self):
        """Test listing recent operations."""
        # Create multiple operations
        op1_id = self.state_manager.create_operation("summary", "user1", "2024-01-01", "2024-01-31")
        op2_id = self.state_manager.create_operation("fetch", "user2", "2024-02-01", "2024-02-28")
        op3_id = self.state_manager.create_operation("summary", "user1", "2024-03-01", "2024-03-31")
        
        # List all operations
        operations = self.state_manager.list_recent_operations()
        assert len(operations) == 3
        
        # Should be newest first
        assert operations[0].id == op3_id
        assert operations[1].id == op2_id
        assert operations[2].id == op1_id
        
        # Test with limit
        operations = self.state_manager.list_recent_operations(limit=2)
        assert len(operations) == 2
        assert operations[0].id == op3_id
        assert operations[1].id == op2_id
        
        # Test with user filter
        operations = self.state_manager.list_recent_operations(user="user1")
        assert len(operations) == 2
        assert all(op.user == "user1" for op in operations)
        
    def test_cleanup_old_operations(self):
        """Test cleaning up old operations."""
        # Create operations with different ages
        recent_id = self.state_manager.create_operation("summary", "user1", "2024-01-01", "2024-01-31")
        
        # Create an old operation by modifying the database directly
        old_date = (datetime.now() - timedelta(days=35)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            old_id = str(uuid.uuid4())
            conn.execute("""
                INSERT INTO operations (
                    id, operation_type, user, since, until, status, created_at,
                    total_repositories, completed_repositories, total_commits
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (old_id, "summary", "user1", "2024-01-01", "2024-01-31", "completed", 
                  old_date, 0, 0, 0))
            
            # Add repository progress for the old operation
            conn.execute("""
                INSERT INTO repository_progress (operation_id, repository_name, status)
                VALUES (?, ?, ?)
            """, (old_id, "owner/repo", "completed"))
            
            conn.commit()
        
        # Cleanup old operations (30 days)
        deleted_count = self.state_manager.cleanup_old_operations(days=30)
        assert deleted_count == 1
        
        # Check that old operation was deleted
        assert self.state_manager.get_operation(old_id) is None
        
        # Check that recent operation still exists
        assert self.state_manager.get_operation(recent_id) is not None
        
        # Check that repository progress was also deleted
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM repository_progress WHERE operation_id = ?",
                (old_id,)
            )
            count = cursor.fetchone()[0]
        assert count == 0


class TestGlobalFunctions:
    """Test global convenience functions."""
    
    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_state.db"
        
        # Mock the config to use our test database
        self.config_patcher = patch('hacktivity.core.state._get_config')
        mock_config = self.config_patcher.start()
        
        # Reset global state manager
        import hacktivity.core.state
        hacktivity.core.state._state_manager = None
        
        # Create a StateManager with our test path
        hacktivity.core.state._state_manager = StateManager(str(self.db_path))
    
    def teardown_method(self):
        """Clean up test environment."""
        self.config_patcher.stop()
        if self.db_path.exists():
            self.db_path.unlink()
    
    def test_create_operation_convenience(self):
        """Test create_operation convenience function."""
        operation_id = create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        
        assert len(operation_id) == 36  # UUID format
        
        # Verify operation was created
        summary = get_operation_status(operation_id)
        assert summary is not None
        assert summary['operation']['operation_type'] == "summary"
    
    def test_update_operation_status_convenience(self):
        """Test update_operation_status convenience function."""
        operation_id = create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        
        update_operation_status(operation_id, "in_progress", total_repositories=3)
        
        summary = get_operation_status(operation_id)
        assert summary['operation']['status'] == "in_progress"
        assert summary['operation']['total_repositories'] == 3
    
    def test_track_repository_progress_convenience(self):
        """Test track_repository_progress convenience function."""
        operation_id = create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        
        # Add repository manually using StateManager for this test
        from hacktivity.core.state import get_state_manager
        get_state_manager().add_repositories_to_operation(operation_id, ["owner/repo"])
        
        track_repository_progress(
            operation_id, 
            "owner/repo", 
            "completed",
            commit_count=42
        )
        
        summary = get_operation_status(operation_id)
        assert summary['repositories_completed'] == 1
        assert summary['operation']['total_commits'] == 42
    
    def test_get_pending_repositories_convenience(self):
        """Test get_pending_repositories convenience function."""
        operation_id = create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        
        # Add repositories manually
        from hacktivity.core.state import get_state_manager
        repositories = ["owner/repo1", "owner/repo2"]
        get_state_manager().add_repositories_to_operation(operation_id, repositories)
        
        # Complete one repository
        track_repository_progress(operation_id, "owner/repo1", "completed")
        
        pending = get_pending_repositories(operation_id)
        assert pending == ["owner/repo2"]


class TestIntegrationScenarios:
    """Test complete workflow scenarios."""
    
    def setup_method(self):
        """Set up test environment."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test_state.db"
        self.state_manager = StateManager(str(self.db_path))
    
    def teardown_method(self):
        """Clean up test environment."""
        if self.db_path.exists():
            self.db_path.unlink()
    
    def test_complete_operation_workflow(self):
        """Test a complete operation from start to finish."""
        # 1. Create operation
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31",
            metadata={"cli_args": ["--format", "json"]}
        )
        
        # 2. Start operation and add repositories
        repositories = ["owner/repo1", "owner/repo2", "owner/repo3"]
        self.state_manager.add_repositories_to_operation(operation_id, repositories)
        self.state_manager.update_operation_status(operation_id, "in_progress")
        
        # 3. Process repositories one by one
        # First repository: successful
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo1", "in_progress", chunk_count=3
        )
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo1", "completed", 
            commit_count=25, completed_chunks=3
        )
        
        # Second repository: failed
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo2", "in_progress", chunk_count=2
        )
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo2", "failed",
            error_message="API rate limit exceeded"
        )
        
        # Third repository: successful
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo3", "in_progress", chunk_count=1
        )
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo3", "completed",
            commit_count=15, completed_chunks=1
        )
        
        # 4. Complete operation
        self.state_manager.update_operation_status(operation_id, "completed")
        
        # 5. Verify final state
        summary = self.state_manager.get_operation_summary(operation_id)
        
        assert summary['operation']['status'] == "completed"
        assert summary['operation']['total_repositories'] == 3
        assert summary['operation']['completed_repositories'] == 2
        assert summary['operation']['total_commits'] == 40  # 25 + 15
        
        assert summary['repositories_completed'] == 2
        assert summary['repositories_failed'] == 1
        assert summary['repositories_pending'] == 0
        assert summary['progress_percentage'] == pytest.approx(66.67, abs=0.01)
        
        # Check pending repositories (should include failed one for retry)
        pending = self.state_manager.get_pending_repositories(operation_id)
        assert pending == ["owner/repo2"]  # Only failed repo should be pending
    
    def test_resume_operation_workflow(self):
        """Test resuming an interrupted operation."""
        # 1. Create operation with repositories
        operation_id = self.state_manager.create_operation(
            operation_type="summary",
            user="testuser",
            since="2024-01-01",
            until="2024-01-31"
        )
        repositories = ["owner/repo1", "owner/repo2", "owner/repo3"]
        self.state_manager.add_repositories_to_operation(operation_id, repositories)
        self.state_manager.update_operation_status(operation_id, "in_progress")
        
        # 2. Partially process (simulate interruption)
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo1", "completed", commit_count=20
        )
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo2", "in_progress", chunk_count=2, completed_chunks=1
        )
        # repo3 remains pending
        
        # 3. Resume: get pending repositories
        pending = self.state_manager.get_pending_repositories(operation_id)
        assert set(pending) == {"owner/repo2", "owner/repo3"}  # in_progress and pending both need processing
        
        # 4. Continue processing
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo2", "completed", 
            commit_count=30, completed_chunks=2
        )
        self.state_manager.update_repository_progress(
            operation_id, "owner/repo3", "completed", commit_count=10
        )
        
        # 5. Complete operation
        self.state_manager.update_operation_status(operation_id, "completed")
        
        # 6. Verify final state
        summary = self.state_manager.get_operation_summary(operation_id)
        assert summary['operation']['status'] == "completed"
        assert summary['operation']['completed_repositories'] == 3
        assert summary['operation']['total_commits'] == 60
        assert summary['repositories_completed'] == 3
        assert summary['progress_percentage'] == 100.0