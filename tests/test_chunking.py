"""Unit tests for chunking module."""

import json
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
import pytest

from hacktivity.core import chunking
from hacktivity.core.chunking import (
    create_date_chunks, process_chunks_with_state, aggregate_chunk_results,
    get_chunk_state_key, DateChunk, ChunkState
)


class TestDateChunk:
    """Test the DateChunk dataclass."""
    
    def test_date_chunk_creation(self):
        """Test creating a DateChunk."""
        chunk = DateChunk('2024-01-01', '2024-01-07', 0)
        assert chunk.since == '2024-01-01'
        assert chunk.until == '2024-01-07'
        assert chunk.index == 0
        
    def test_date_chunk_str(self):
        """Test string representation."""
        chunk = DateChunk('2024-01-01', '2024-01-07', 0)
        assert str(chunk) == "DateChunk(0: 2024-01-01 to 2024-01-07)"


class TestChunkState:
    """Test the ChunkState dataclass."""
    
    def test_chunk_state_creation(self):
        """Test creating a ChunkState."""
        state = ChunkState(
            chunk_index=0,
            status='completed',
            start_time='2024-01-01T10:00:00Z',
            end_time='2024-01-01T10:05:00Z',
            commit_count=15
        )
        assert state.chunk_index == 0
        assert state.status == 'completed'
        assert state.commit_count == 15
        
    def test_chunk_state_defaults(self):
        """Test ChunkState with default values."""
        state = ChunkState(chunk_index=1, status='pending')
        assert state.chunk_index == 1
        assert state.status == 'pending'
        assert state.start_time is None
        assert state.end_time is None
        assert state.commit_count == 0
        assert state.error_message is None


class TestCreateDateChunks:
    """Test the create_date_chunks function."""
    
    def test_create_weekly_chunks_full_year(self):
        """Test creating weekly chunks for a full year."""
        chunks = create_date_chunks('2024-01-01', '2024-12-31', max_days=7)
        
        # Should have 53 weeks (2024 is a leap year)
        assert len(chunks) >= 52
        assert len(chunks) <= 53
        
        # Check first chunk
        assert chunks[0].since == '2024-01-01'
        assert chunks[0].until == '2024-01-07'
        assert chunks[0].index == 0
        
        # Check last chunk should end at or before 2024-12-31
        last_chunk = chunks[-1]
        assert last_chunk.until <= '2024-12-31'
        assert last_chunk.index == len(chunks) - 1
        
    def test_create_daily_chunks(self):
        """Test creating daily chunks."""
        chunks = create_date_chunks('2024-01-01', '2024-01-05', max_days=1)
        
        assert len(chunks) == 5
        
        expected_dates = [
            ('2024-01-01', '2024-01-01'),
            ('2024-01-02', '2024-01-02'),
            ('2024-01-03', '2024-01-03'),
            ('2024-01-04', '2024-01-04'),
            ('2024-01-05', '2024-01-05')
        ]
        
        for i, (expected_since, expected_until) in enumerate(expected_dates):
            assert chunks[i].since == expected_since
            assert chunks[i].until == expected_until
            assert chunks[i].index == i
            
    def test_create_chunks_smaller_than_max(self):
        """Test creating chunks when range is smaller than max_days."""
        chunks = create_date_chunks('2024-01-01', '2024-01-03', max_days=7)
        
        # Should create one chunk covering the whole range
        assert len(chunks) == 1
        assert chunks[0].since == '2024-01-01'
        assert chunks[0].until == '2024-01-03'
        assert chunks[0].index == 0
        
    def test_create_chunks_single_day(self):
        """Test creating chunks for a single day."""
        chunks = create_date_chunks('2024-01-01', '2024-01-01', max_days=7)
        
        assert len(chunks) == 1
        assert chunks[0].since == '2024-01-01'
        assert chunks[0].until == '2024-01-01'
        assert chunks[0].index == 0
        
    def test_create_chunks_invalid_range(self):
        """Test error handling for invalid date ranges."""
        with pytest.raises(ValueError):
            create_date_chunks('2024-01-05', '2024-01-01', max_days=7)  # since > until
            
    def test_create_chunks_different_max_days(self):
        """Test creating chunks with different max_days values."""
        # 14-day chunks
        chunks_14 = create_date_chunks('2024-01-01', '2024-01-28', max_days=14)
        assert len(chunks_14) == 2
        assert chunks_14[0].until == '2024-01-14'
        assert chunks_14[1].since == '2024-01-15'
        assert chunks_14[1].until == '2024-01-28'
        
        # 30-day chunks  
        chunks_30 = create_date_chunks('2024-01-01', '2024-03-31', max_days=30)
        assert len(chunks_30) == 4  # Jan 1-30, Jan 31-Feb 29, Mar 1-30, Mar 31


class TestChunkStateKey:
    """Test chunk state key generation."""
    
    def test_get_chunk_state_key(self):
        """Test generating chunk state keys."""
        key = get_chunk_state_key('owner/repo', '2024-01-01', '2024-12-31', 'user1')
        assert key == 'chunk_state:owner/repo:2024-01-01:2024-12-31:user1'
        
        # Test with no author filter
        key_no_author = get_chunk_state_key('owner/repo', '2024-01-01', '2024-12-31')
        assert key_no_author == 'chunk_state:owner/repo:2024-01-01:2024-12-31:all'


class TestAggregateChunkResults:
    """Test chunk result aggregation."""
    
    def test_aggregate_empty_results(self):
        """Test aggregating empty chunk results."""
        chunk_results = {}
        aggregated = aggregate_chunk_results(chunk_results)
        
        assert aggregated == []
        
    def test_aggregate_single_chunk(self):
        """Test aggregating results from a single chunk."""
        chunk_results = {
            0: [
                {'sha': 'abc123', 'message': 'First commit', 'commit_date': '2024-01-01T10:00:00Z'},
                {'sha': 'def456', 'message': 'Second commit', 'commit_date': '2024-01-01T15:00:00Z'}
            ]
        }
        
        aggregated = aggregate_chunk_results(chunk_results)
        
        assert len(aggregated) == 2
        # Should be sorted by commit_date (newest first)
        assert aggregated[0]['sha'] == 'def456'  # 15:00 comes first
        assert aggregated[1]['sha'] == 'abc123'  # 10:00 comes second
        
    def test_aggregate_multiple_chunks(self):
        """Test aggregating results from multiple chunks."""
        chunk_results = {
            0: [
                {'sha': 'abc123', 'message': 'First commit', 'commit_date': '2024-01-01T10:00:00Z'},
            ],
            1: [
                {'sha': 'def456', 'message': 'Second commit', 'commit_date': '2024-01-02T10:00:00Z'},
                {'sha': 'ghi789', 'message': 'Third commit', 'commit_date': '2024-01-02T15:00:00Z'},
            ],
            2: [
                {'sha': 'jkl012', 'message': 'Fourth commit', 'commit_date': '2024-01-03T10:00:00Z'},
            ]
        }
        
        aggregated = aggregate_chunk_results(chunk_results)
        
        assert len(aggregated) == 4
        # Should be sorted by commit_date (newest first)
        expected_order = ['jkl012', 'ghi789', 'def456', 'abc123']
        actual_order = [commit['sha'] for commit in aggregated]
        assert actual_order == expected_order
        
    def test_aggregate_with_missing_date(self):
        """Test aggregating commits with missing or malformed dates."""
        chunk_results = {
            0: [
                {'sha': 'abc123', 'message': 'Good commit', 'commit_date': '2024-01-01T10:00:00Z'},
                {'sha': 'def456', 'message': 'No date commit'},  # Missing commit_date
                {'sha': 'ghi789', 'message': 'Bad date commit', 'commit_date': 'invalid-date'},
            ]
        }
        
        aggregated = aggregate_chunk_results(chunk_results)
        
        assert len(aggregated) == 3
        # Commits with missing/invalid dates should be sorted to the end
        assert aggregated[0]['sha'] == 'abc123'  # Good date comes first
        # The other two should be at the end in original order
        assert aggregated[1]['sha'] in ['def456', 'ghi789']
        assert aggregated[2]['sha'] in ['def456', 'ghi789']


class TestProcessChunksWithState:
    """Test processing chunks with state management."""
    
    @patch('hacktivity.core.chunking.cache.get')
    @patch('hacktivity.core.chunking.cache.get_cache')
    @patch('hacktivity.core.chunking._get_fetch_function')
    def test_process_chunks_no_existing_state(self, mock_get_fetch_fn, mock_cache_get_cache, mock_cache_get):
        """Test processing chunks when no previous state exists."""
        # Setup: No existing state
        mock_cache_get.return_value = None
        
        # Setup: Mock cache interface
        mock_cache_instance = MagicMock()
        mock_cache_get_cache.return_value = mock_cache_instance
        
        # Setup: Mock commit fetching function
        mock_fetch = MagicMock()
        mock_fetch.side_effect = [
            [{'sha': 'abc123', 'commit_date': '2024-01-01T10:00:00Z'}],  # Chunk 0
            [{'sha': 'def456', 'commit_date': '2024-01-08T10:00:00Z'}],  # Chunk 1
        ]
        mock_get_fetch_fn.return_value = mock_fetch
        
        # Create test chunks
        chunks = [
            DateChunk('2024-01-01', '2024-01-07', 0),
            DateChunk('2024-01-08', '2024-01-14', 1)
        ]
        
        result = process_chunks_with_state(
            'owner/repo', '2024-01-01', '2024-01-14', 'user1', chunks
        )
        
        # Should return aggregated results
        assert len(result) == 2
        assert result[0]['sha'] == 'def456'  # Newer date first
        assert result[1]['sha'] == 'abc123'
        
        # Should have called fetch for each chunk
        assert mock_fetch.call_count == 2
        mock_fetch.assert_any_call('owner/repo', '2024-01-01', '2024-01-07', 'user1')
        mock_fetch.assert_any_call('owner/repo', '2024-01-08', '2024-01-14', 'user1')
        
        # Should have saved state  
        assert mock_cache_instance.set.call_count >= 1
        
    @patch('hacktivity.core.chunking.cache.get')
    @patch('hacktivity.core.chunking.cache.get_cache')
    @patch('hacktivity.core.chunking._get_fetch_function')
    def test_process_chunks_with_partial_state(self, mock_get_fetch_fn, mock_cache_get_cache, mock_cache_get):
        """Test processing chunks when some chunks are already completed."""
        # Setup: Existing state with chunk 0 completed
        existing_state = {
            'chunks': {
                '0': {
                    'chunk_index': 0,
                    'status': 'completed',
                    'start_time': '2024-01-01T09:00:00Z',
                    'end_time': '2024-01-01T09:05:00Z',
                    'commit_count': 1,
                    'error_message': None
                }
            },
            'chunk_results': {
                '0': [{'sha': 'abc123', 'commit_date': '2024-01-01T10:00:00Z'}]
            }
        }
        mock_cache_get.return_value = existing_state
        
        # Setup: Mock cache interface
        mock_cache_instance = MagicMock()
        mock_cache_get_cache.return_value = mock_cache_instance
        
        # Setup: Mock commit fetching for only incomplete chunks
        mock_fetch = MagicMock()
        mock_fetch.return_value = [{'sha': 'def456', 'commit_date': '2024-01-08T10:00:00Z'}]
        mock_get_fetch_fn.return_value = mock_fetch
        
        # Create test chunks
        chunks = [
            DateChunk('2024-01-01', '2024-01-07', 0),  # Already completed
            DateChunk('2024-01-08', '2024-01-14', 1)   # Needs processing
        ]
        
        result = process_chunks_with_state(
            'owner/repo', '2024-01-01', '2024-01-14', 'user1', chunks
        )
        
        # Should return aggregated results including cached chunk
        assert len(result) == 2
        
        # Should only fetch incomplete chunks
        assert mock_fetch.call_count == 1
        mock_fetch.assert_called_with('owner/repo', '2024-01-08', '2024-01-14', 'user1')
        
    @patch('hacktivity.core.chunking.cache.get')
    @patch('hacktivity.core.chunking.cache.get_cache')
    @patch('hacktivity.core.chunking._get_fetch_function')
    def test_process_chunks_with_error_handling(self, mock_get_fetch_fn, mock_cache_get_cache, mock_cache_get):
        """Test processing chunks with error handling."""
        # Setup: No existing state
        mock_cache_get.return_value = None
        
        # Setup: Mock cache interface
        mock_cache_instance = MagicMock()
        mock_cache_get_cache.return_value = mock_cache_instance
        
        # Setup: Mock commit fetching with one failure
        mock_fetch = MagicMock()
        mock_fetch.side_effect = [
            [{'sha': 'abc123', 'commit_date': '2024-01-01T10:00:00Z'}],  # Chunk 0 succeeds
            Exception("API error"),  # Chunk 1 fails
        ]
        mock_get_fetch_fn.return_value = mock_fetch
        
        # Create test chunks
        chunks = [
            DateChunk('2024-01-01', '2024-01-07', 0),
            DateChunk('2024-01-08', '2024-01-14', 1)
        ]
        
        result = process_chunks_with_state(
            'owner/repo', '2024-01-01', '2024-01-14', 'user1', chunks
        )
        
        # Should return results from successful chunks only
        assert len(result) == 1
        assert result[0]['sha'] == 'abc123'
        
        # Should have attempted both chunks
        assert mock_fetch.call_count == 2
        
        # Should have saved state with error information
        assert mock_cache_instance.set.call_count >= 1


class TestIntegrationWithExistingModules:
    """Test integration with existing commit fetching."""
    
    def test_chunked_fetch_interface(self):
        """Test that chunking integrates with existing interfaces."""
        # This is more of a design test - ensuring the interface makes sense
        
        # The main integration function should work like:
        # chunks = create_date_chunks('2024-01-01', '2024-12-31', max_days=7)
        # result = process_chunks_with_state('owner/repo', '2024-01-01', '2024-12-31', 'user1', chunks)
        
        # This should be equivalent to calling fetch_repo_commits for the full range
        # but with the ability to resume if interrupted
        
        chunks = create_date_chunks('2024-01-01', '2024-01-14', max_days=7)
        assert len(chunks) == 2
        assert all(isinstance(chunk, DateChunk) for chunk in chunks)
        
        # The interface should be consistent
        for chunk in chunks:
            assert hasattr(chunk, 'since')
            assert hasattr(chunk, 'until') 
            assert hasattr(chunk, 'index')