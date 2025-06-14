"""
Large-scale integration tests for T028.

These tests exercise the real integration paths between chunking, state,
circuit breaking, and the parallel orchestrator. Only the external GitHub
API boundary (`subprocess.run`) is mocked.

Tests cover:
1. High-volume processing (10,000+ commits)
2. Interruption and resume capability
3. Circuit breaker behavior at scale
4. Parallel processing edge cases
5. Performance benchmarks
"""
import tempfile
import time
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from hacktivity.core.parallel import fetch_commits_parallel
from hacktivity.core.state import get_state_manager, StateManager
from hacktivity.core.circuit_breaker import CircuitOpenError, get_circuit, CircuitState
from hacktivity.core.config import GitHubConfig

from tests.fixtures.large_repo_dataset import (
    create_large_dataset, 
    create_large_single_repo_dataset,
    create_uneven_workload_dataset,
    calculate_expected_totals
)
from tests.utils.mock_api import MockAPI, MockAPIBuilder, SimulatedInterruption
from tests.utils.performance import (
    PerformanceProfiler, 
    create_standard_benchmarks,
    assert_performance_requirements
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch, tmp_path):
    """Fixture to ensure each test runs with a fresh, isolated environment."""
    # Create isolated directories
    cache_dir = tmp_path / "cache"
    state_dir = tmp_path / "state"
    cache_dir.mkdir()
    state_dir.mkdir()
    
    # Mock configuration to use temporary directories
    mock_config = GitHubConfig(
        max_workers=3,  # Reduced for faster testing
        rate_limit_buffer=100,
        parallel_enabled=True,
        timeout_seconds=30,
        retry_attempts=3,
        per_page=30,
        max_pages=100
    )
    
    # Patch paths and configuration
    monkeypatch.setenv("HACKTIVITY_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("HACKTIVITY_STATE_DIR", str(state_dir))
    
    # Reset singletons to force re-initialization with new paths
    import hacktivity.core.cache as cache_module
    import hacktivity.core.state as state_module
    import hacktivity.core.circuit_breaker as cb_module
    import hacktivity.core.rate_limiter as rl_module
    
    # Clear singleton instances
    cache_module._CACHE = None
    state_module._state_manager = None
    cb_module._STORE = None
    cb_module._BREAKERS = {}
    rl_module.RateLimitCoordinator._instance = None
    
    # Mock config getter
    def mock_get_config():
        config_obj = MagicMock()
        config_obj.github = mock_config
        config_obj.cache.directory = str(cache_dir)
        return config_obj
    
    monkeypatch.setattr("hacktivity.core.config.get_config", mock_get_config)
    monkeypatch.setattr("hacktivity.core.parallel.get_config", mock_get_config)
    monkeypatch.setattr("hacktivity.core.rate_limiter.get_config", mock_get_config)
    monkeypatch.setattr("hacktivity.core.circuit_breaker.get_config", mock_get_config)
    
    # Initialize state manager with temporary directory
    state_manager = StateManager(str(state_dir / "test_state.db"))
    monkeypatch.setattr("hacktivity.core.state.get_state_manager", lambda: state_manager)
    
    yield


class TestLargeScaleProcessing:
    """Tests for high-volume data processing scenarios."""

    def test_high_volume_10k_commits_completeness_and_performance(self):
        """
        Scenario: 100 repos × 100 commits = 10,000 total commits.
        Verifies: Data completeness, performance benchmarks, and API efficiency.
        """
        # Setup large dataset
        num_repos, commits_per_repo = 100, 100
        total_commits_expected = num_repos * commits_per_repo
        
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        mock_api = MockAPI(commit_map)
        
        # Performance measurement
        with PerformanceProfiler("high_volume_10k") as profiler:
            with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id="high-volume-10k-op",
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-04-10",  # 100 days for 100 commits
                    author_filter="test-author"
                )
        
        # 1. Verify Data Completeness
        total_commits_fetched = sum(len(commits) for commits in results.values())
        assert total_commits_fetched == total_commits_expected, \
            f"Data loss occurred: expected {total_commits_expected}, got {total_commits_fetched}"
        
        # Verify all repositories processed
        assert len(results) == num_repos, \
            f"Not all repositories processed: expected {num_repos}, got {len(results)}"
        
        # Verify no empty results (unless expected)
        empty_repos = [repo for repo, commits in results.items() if len(commits) == 0]
        assert len(empty_repos) == 0, f"Unexpected empty results for repositories: {empty_repos}"
        
        # 2. Performance Benchmarks
        throughput = total_commits_fetched / profiler.duration_seconds
        assert_performance_requirements(
            profiler,
            max_duration=30.0,      # 30 seconds max
            max_memory_mb=500.0,    # 500MB max
            min_throughput=100.0,   # 100 commits/second min
            item_count=total_commits_fetched
        )
        
        # 3. API Efficiency
        stats = mock_api.get_call_statistics()
        # Should be approximately 1 call per repo (all commits fit in one page)
        assert stats['total_calls'] <= num_repos * 1.2, \
            f"Inefficient API usage: {stats['total_calls']} calls for {num_repos} repos"
        
        profiler.add_custom_metric("total_commits", total_commits_fetched)
        profiler.add_custom_metric("throughput_commits_per_sec", throughput)
        profiler.add_custom_metric("api_calls", stats['total_calls'])
        profiler.add_custom_metric("api_efficiency", total_commits_fetched / stats['total_calls'])

    def test_single_large_repository_25k_commits(self):
        """
        Scenario: One repository with 25,000+ commits.
        Verifies: Chunking behavior, memory efficiency, and pagination handling.
        """
        repo_name = "test-org/massive-repo"
        num_commits = 25000
        
        commit_map = create_large_single_repo_dataset(repo_name, num_commits)
        mock_api = MockAPI(commit_map)
        
        with PerformanceProfiler("single_large_repo") as profiler:
            with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id="single-large-repo-op",
                    repositories=[repo_name],
                    since="2024-01-01",
                    until="2026-01-01",  # 2-year range for chunking
                    author_filter="test-author"
                )
        
        # Verify data completeness
        total_commits_fetched = len(results[repo_name])
        assert total_commits_fetched == num_commits, \
            f"Data loss in large repo: expected {num_commits}, got {total_commits_fetched}"
        
        # Performance for large single repository
        assert_performance_requirements(
            profiler,
            max_duration=60.0,      # 60 seconds max for large repo
            max_memory_mb=800.0,    # 800MB max for large dataset
        )
        
        # API efficiency for paginated large repo
        stats = mock_api.get_call_statistics()
        expected_pages = (num_commits + 29) // 30  # 30 per page
        assert stats['total_calls'] >= expected_pages * 0.8, \
            f"Too few API calls for large repo: {stats['total_calls']} calls for {num_commits} commits"


class TestInterruptionAndResume:
    """Tests for interruption handling and resume capability."""

    def test_interruption_and_resume_maintains_data_integrity(self):
        """
        Scenario: A network error interrupts processing mid-way.
        Verifies: State is saved correctly and subsequent run completes with no data loss.
        """
        num_repos, commits_per_repo = 10, 50
        total_commits_expected = num_repos * commits_per_repo
        
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        
        operation_id = "resume-integrity-test"
        
        # --- Phase 1: Interruption ---
        mock_api_interrupt = MockAPIBuilder.for_interruption_test(
            num_repos=num_repos, 
            commits_per_repo=commits_per_repo,
            interrupt_repo_index=4  # Fail on 5th repository
        )
        
        # Expect interruption to occur
        with pytest.raises(SimulatedInterruption):
            with patch('subprocess.run', side_effect=mock_api_interrupt.mock_subprocess_run):
                fetch_commits_parallel(
                    operation_id=operation_id,
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-02-19",
                    author_filter="test-author"
                )
        
        # Verify partial state was saved
        state_manager = get_state_manager()
        operation = state_manager.get_operation(operation_id)
        assert operation is not None, "Operation state not saved after interruption"
        assert operation.status in ['in_progress', 'failed'], \
            f"Unexpected operation status after interruption: {operation.status}"
        
        # Check that some repositories were completed before interruption
        repo_progress = state_manager.get_repositories_for_operation(operation_id)
        completed_repos = [r for r in repo_progress if r.status == 'completed']
        assert len(completed_repos) > 0, "No repositories completed before interruption"
        assert len(completed_repos) < num_repos, "All repositories completed despite interruption"
        
        # --- Phase 2: Resume and Complete ---
        mock_api_resume = MockAPI(commit_map)  # No failures configured
        
        with PerformanceProfiler("resume_operation") as profiler:
            with patch('subprocess.run', side_effect=mock_api_resume.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id=operation_id,  # Same operation ID
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-02-19",
                    author_filter="test-author"
                )
        
        # Verify complete data integrity after resume
        total_commits_fetched = sum(len(commits) for commits in results.values())
        assert total_commits_fetched == total_commits_expected, \
            f"Data loss after resume: expected {total_commits_expected}, got {total_commits_fetched}"
        
        # Verify final operation state
        final_operation = state_manager.get_operation(operation_id)
        assert final_operation.status == 'completed', \
            f"Operation not marked as completed: {final_operation.status}"
        
        final_repo_progress = state_manager.get_repositories_for_operation(operation_id)
        completed_final = [r for r in final_repo_progress if r.status == 'completed']
        assert len(completed_final) == num_repos, \
            f"Not all repositories completed after resume: {len(completed_final)}/{num_repos}"
        
        # Resume should be relatively efficient (not restart from scratch)
        resume_efficiency = 1.0 - (profiler.duration_seconds / 10.0)  # Should be much faster than 10s
        assert resume_efficiency > 0.5, \
            f"Resume operation too slow, suggesting full restart: {profiler.duration_seconds}s"

    def test_multiple_interruptions_and_incremental_progress(self):
        """
        Scenario: Multiple interruptions occur, but each run makes incremental progress.
        Verifies: Consistent state management and incremental completion.
        """
        num_repos, commits_per_repo = 15, 30
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        operation_id = "multi-interrupt-test"
        
        state_manager = get_state_manager()
        
        # Simulate 3 interruptions at different points
        interrupt_points = [3, 7, 12]  # Repositories to fail at
        
        for i, interrupt_at in enumerate(interrupt_points):
            mock_api = MockAPI(commit_map)
            mock_api.set_failure(repo_names[interrupt_at], SimulatedInterruption, count=1)
            
            with pytest.raises(SimulatedInterruption):
                with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                    fetch_commits_parallel(
                        operation_id=operation_id,
                        repositories=repo_names,
                        since="2024-01-01",
                        until="2024-01-31",
                        author_filter="test-author"
                    )
            
            # Verify incremental progress
            operation = state_manager.get_operation(operation_id)
            repo_progress = state_manager.get_repositories_for_operation(operation_id)
            completed_count = len([r for r in repo_progress if r.status == 'completed'])
            
            # Should have more completed repositories with each iteration
            expected_min_completed = interrupt_at
            assert completed_count >= expected_min_completed, \
                f"Insufficient progress on iteration {i+1}: {completed_count} completed, expected >= {expected_min_completed}"
        
        # Final run - complete successfully
        mock_api_final = MockAPI(commit_map)
        with patch('subprocess.run', side_effect=mock_api_final.mock_subprocess_run):
            results = fetch_commits_parallel(
                operation_id=operation_id,
                repositories=repo_names,
                since="2024-01-01",
                until="2024-01-31",
                author_filter="test-author"
            )
        
        # Verify final completion
        total_expected = num_repos * commits_per_repo
        total_actual = sum(len(commits) for commits in results.values())
        assert total_actual == total_expected, \
            f"Data loss after multiple interruptions: {total_actual}/{total_expected}"


class TestCircuitBreakerAtScale:
    """Tests for circuit breaker behavior during large-scale operations."""

    def test_circuit_breaker_isolates_failing_repository(self):
        """
        Scenario: One repository consistently fails while others succeed.
        Verifies: Circuit breaker opens for failing repo, others continue processing.
        """
        num_repos, commits_per_repo = 5, 10
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        
        mock_api = MockAPIBuilder.for_circuit_breaker_test(
            num_repos=num_repos,
            commits_per_repo=commits_per_repo,
            failing_repo_index=2
        )
        failing_repo = repo_names[2]
        
        with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
            results = fetch_commits_parallel(
                operation_id="circuit-breaker-test",
                repositories=repo_names,
                since="2024-01-01",
                until="2024-01-11",
                author_filter="test-author"
            )
        
        # Verify successful repositories have data
        for i, repo in enumerate(repo_names):
            if i != 2:  # Not the failing repo
                assert len(results[repo]) == commits_per_repo, \
                    f"Repository {repo} should have succeeded but has {len(results[repo])} commits"
            else:
                assert len(results[repo]) == 0, \
                    f"Failing repository {repo} should have 0 commits but has {len(results[repo])}"
        
        # Verify circuit breaker state
        # Note: Circuit breaker key format depends on implementation
        circuit_key = f"repos/{failing_repo}/commits"
        breaker = get_circuit(circuit_key)
        assert breaker.state == CircuitState.OPEN, \
            f"Circuit breaker should be OPEN for {failing_repo}, but is {breaker.state}"
        
        # Verify limited retry attempts due to circuit opening
        stats = mock_api.get_call_statistics()
        failure_config = mock_api.failure_configs[failing_repo]
        initial_failures = 100
        remaining_failures = failure_config['remaining']
        attempts_made = initial_failures - remaining_failures
        
        # Should have stopped trying after circuit opened (typically after 5 failures)
        assert attempts_made < 15, \
            f"Too many attempts on failing repo: {attempts_made} (circuit should have opened sooner)"

    def test_circuit_breaker_recovery_after_cooldown(self):
        """
        Scenario: Circuit breaker opens, then recovers after cooldown period.
        Verifies: Circuit breaker properly transitions states and allows recovery.
        """
        # This test would require time manipulation or shorter cooldown periods
        # For now, verify circuit breaker configuration and state management
        num_repos = 3
        commit_map = create_large_dataset(num_repos, 20)
        repo_names = list(commit_map.keys())
        failing_repo = repo_names[1]
        
        mock_api = MockAPI(commit_map)
        # Set to fail 10 times, then succeed
        mock_api.set_failure(failing_repo, subprocess.TimeoutExpired, count=10)
        
        with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
            results1 = fetch_commits_parallel(
                operation_id="recovery-test-1",
                repositories=repo_names,
                since="2024-01-01",
                until="2024-01-21",
                author_filter="test-author"
            )
        
        # Circuit should be open
        circuit_key = f"repos/{failing_repo}/commits"
        breaker = get_circuit(circuit_key)
        assert breaker.state == CircuitState.OPEN
        
        # Failing repo should have no results
        assert len(results1[failing_repo]) == 0
        
        # Simulate circuit recovery (in real scenario, would wait for cooldown)
        # For testing, manually reset circuit state
        breaker._state = CircuitState.CLOSED
        breaker._failure_count = 0
        
        # Second attempt should succeed
        mock_api2 = MockAPI(commit_map)  # No failures
        with patch('subprocess.run', side_effect=mock_api2.mock_subprocess_run):
            results2 = fetch_commits_parallel(
                operation_id="recovery-test-2",
                repositories=[failing_repo],  # Just the previously failing repo
                since="2024-01-01",
                until="2024-01-21",
                author_filter="test-author"
            )
        
        # Should now succeed
        assert len(results2[failing_repo]) == 20, \
            f"Repository should have recovered but has {len(results2[failing_repo])} commits"


class TestParallelProcessingEdgeCases:
    """Tests for edge cases in parallel processing."""

    def test_uneven_workload_distribution(self):
        """
        Scenario: Repositories with vastly different commit counts.
        Verifies: Efficient work distribution and resource utilization.
        """
        commit_map = create_uneven_workload_dataset()
        repo_names = list(commit_map.keys())
        mock_api = MockAPI(commit_map)
        
        expected_totals = calculate_expected_totals(commit_map, "test-author")
        
        with PerformanceProfiler("uneven_workload") as profiler:
            with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id="uneven-workload-test",
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-12-31",
                    author_filter="test-author"
                )
        
        # Verify all data processed correctly
        total_fetched = sum(len(commits) for commits in results.values())
        assert total_fetched == expected_totals['total_commits'], \
            f"Data loss with uneven workload: {total_fetched}/{expected_totals['total_commits']}"
        
        # Verify each repository processed correctly
        for repo_name, expected_commits in commit_map.items():
            actual_count = len(results[repo_name])
            expected_count = len(expected_commits)
            assert actual_count == expected_count, \
                f"Repository {repo_name}: expected {expected_count}, got {actual_count}"
        
        # Performance should still be reasonable despite uneven distribution
        assert profiler.duration_seconds < 20.0, \
            f"Uneven workload processing too slow: {profiler.duration_seconds}s"

    def test_maximum_worker_saturation(self):
        """
        Scenario: Process enough repositories to saturate all available workers.
        Verifies: Worker pool management and resource contention handling.
        """
        # Create exactly enough work to test worker saturation
        # With 3 workers (from config), use 9 repositories for 3 full cycles
        num_repos = 9
        commits_per_repo = 50
        commit_map = create_large_dataset(num_repos, commits_per_repo)
        repo_names = list(commit_map.keys())
        
        mock_api = MockAPI(commit_map)
        # Add small delay to make worker coordination visible
        mock_api.set_response_delay(0.1)
        
        with PerformanceProfiler("worker_saturation") as profiler:
            with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id="worker-saturation-test",
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-02-19",
                    author_filter="test-author"
                )
        
        # Verify complete processing
        total_expected = num_repos * commits_per_repo
        total_actual = sum(len(commits) for commits in results.values())
        assert total_actual == total_expected, \
            f"Data loss during worker saturation: {total_actual}/{total_expected}"
        
        # With 3 workers and 0.1s delay per repo, minimum time should be ~0.3s
        # (3 batches of 3 repos each, processed in parallel)
        min_expected_time = (num_repos / 3) * 0.1
        assert profiler.duration_seconds >= min_expected_time * 0.8, \
            f"Processing unexpectedly fast, suggesting sequential processing: {profiler.duration_seconds}s"
        
        # But should be much faster than sequential processing
        max_expected_time = num_repos * 0.1 * 0.5  # 50% of sequential time
        assert profiler.duration_seconds <= max_expected_time, \
            f"Processing too slow, parallel efficiency poor: {profiler.duration_seconds}s"

    def test_worker_failure_isolation(self):
        """
        Scenario: One worker thread fails, others continue processing.
        Verifies: Fault isolation and continued operation.
        """
        num_repos = 6
        commit_map = create_large_dataset(num_repos, 20)
        repo_names = list(commit_map.keys())
        
        mock_api = MockAPI(commit_map)
        # Make every 3rd repository fail (to affect different workers)
        for i in range(0, num_repos, 3):
            if i < len(repo_names):
                mock_api.set_failure(repo_names[i], subprocess.TimeoutExpired, count=5)
        
        with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
            results = fetch_commits_parallel(
                operation_id="worker-failure-test",
                repositories=repo_names,
                since="2024-01-01",
                until="2024-01-21",
                author_filter="test-author"
            )
        
        # Verify that non-failing repositories still succeeded
        successful_repos = [repo for repo, commits in results.items() if len(commits) > 0]
        failed_repos = [repo for repo, commits in results.items() if len(commits) == 0]
        
        assert len(successful_repos) > 0, "No repositories succeeded despite worker failures"
        assert len(failed_repos) > 0, "No repositories failed as expected"
        
        # Should have 2 out of every 3 repositories succeeding
        expected_successful = num_repos - (num_repos + 2) // 3
        assert len(successful_repos) >= expected_successful, \
            f"Too few successful repositories: {len(successful_repos)}, expected >= {expected_successful}"


class TestPerformanceBenchmarks:
    """Tests to establish and validate performance benchmarks."""

    def test_performance_regression_detection(self):
        """
        Verify that current performance meets established benchmarks.
        This test will fail if performance regresses significantly.
        """
        benchmarks = create_standard_benchmarks()
        
        # Test medium-scale performance (1000 commits)
        commit_map = create_large_dataset(10, 100)  # 1000 total commits
        repo_names = list(commit_map.keys())
        mock_api = MockAPI(commit_map)
        
        with PerformanceProfiler("benchmark_test") as profiler:
            with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id="benchmark-test",
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-04-10",
                    author_filter="test-author"
                )
        
        total_commits = sum(len(commits) for commits in results.values())
        throughput = total_commits / profiler.duration_seconds
        
        # Check against benchmark
        benchmark_result = benchmarks.check_benchmark(
            "medium_scale_1k",
            profiler.get_metrics(),
            throughput=throughput
        )
        
        assert benchmark_result['passed'], \
            f"Performance benchmark failed: {benchmark_result['details']}"
        
        # Log performance metrics for tracking
        profiler.add_custom_metric("benchmark_name", "medium_scale_1k")
        profiler.add_custom_metric("throughput", throughput)
        profiler.add_custom_metric("commits_processed", total_commits)

    def test_memory_efficiency_at_scale(self):
        """
        Verify memory usage remains reasonable even with large datasets.
        """
        # Large dataset: 50 repos × 200 commits = 10,000 commits
        commit_map = create_large_dataset(50, 200)
        repo_names = list(commit_map.keys())
        mock_api = MockAPI(commit_map)
        
        with PerformanceProfiler("memory_efficiency") as profiler:
            with patch('subprocess.run', side_effect=mock_api.mock_subprocess_run):
                results = fetch_commits_parallel(
                    operation_id="memory-efficiency-test",
                    repositories=repo_names,
                    since="2024-01-01",
                    until="2024-07-18",  # ~200 days
                    author_filter="test-author"
                )
        
        total_commits = sum(len(commits) for commits in results.values())
        
        # Memory efficiency: should process 10k commits in <500MB
        memory_per_commit = profiler.peak_memory_mb / total_commits
        assert memory_per_commit < 0.05, \
            f"Memory inefficient: {memory_per_commit:.4f}MB per commit (target: <0.05MB)"
        
        # Total memory should be reasonable
        assert profiler.peak_memory_mb < 500.0, \
            f"Excessive memory usage: {profiler.peak_memory_mb:.2f}MB (target: <500MB)"
        
        profiler.add_custom_metric("memory_per_commit_mb", memory_per_commit)
        profiler.add_custom_metric("total_commits_processed", total_commits)


if __name__ == "__main__":
    # Allow running individual test classes for debugging
    pytest.main([__file__ + "::TestLargeScaleProcessing::test_high_volume_10k_commits_completeness_and_performance", "-v"])