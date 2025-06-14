"""Unit tests for AI batch processing module."""

import pytest
from unittest.mock import patch, MagicMock
import time

# Mock dependencies at module level
with patch.dict('sys.modules', {
    'google.generativeai': MagicMock(),
}):
    from hacktivity.core.ai import (
        _split_commits_into_batches, 
        _hash_content,
        _generate_batch_cache_key,
        get_batch_summary,
        get_batched_summary,
        _aggregate_batch_summaries
    )


class TestBatchUtilities:
    """Test utility functions for batch processing."""
    
    def test_hash_content(self):
        """Test content hashing is consistent."""
        content1 = "test content"
        content2 = "test content"
        content3 = "different content"
        
        hash1 = _hash_content(content1)
        hash2 = _hash_content(content2)
        hash3 = _hash_content(content3)
        
        assert hash1 == hash2  # Same content should produce same hash
        assert hash1 != hash3  # Different content should produce different hash
        assert len(hash1) == 64  # SHA256 produces 64-character hex string
    
    def test_generate_batch_cache_key(self):
        """Test batch cache key generation."""
        commits_hash = "abcdef123456789"
        prompt_hash = "xyz789"
        batch_index = 5
        
        key = _generate_batch_cache_key(commits_hash, prompt_hash, batch_index)
        
        assert "ai_batch:" in key
        assert commits_hash[:16] in key
        assert prompt_hash[:8] in key
        assert str(batch_index) in key
    
    def test_split_commits_no_batching_needed(self):
        """Test splitting when commits fit in single batch."""
        commits = ["commit1", "commit2", "commit3"]
        batch_size = 5
        
        batches = _split_commits_into_batches(commits, batch_size)
        
        assert len(batches) == 1
        assert batches[0] == commits
    
    def test_split_commits_multiple_batches(self):
        """Test splitting commits into multiple batches."""
        commits = [f"commit{i}" for i in range(10)]
        batch_size = 3
        
        batches = _split_commits_into_batches(commits, batch_size)
        
        assert len(batches) == 4  # 10 commits, 3 per batch = 4 batches
        assert batches[0] == commits[0:3]
        assert batches[1] == commits[3:6]
        assert batches[2] == commits[6:9]
        assert batches[3] == commits[9:10]
    
    def test_split_commits_with_overlap(self):
        """Test splitting commits with overlap between batches."""
        commits = [f"commit{i}" for i in range(10)]
        batch_size = 4
        overlap = 1
        
        batches = _split_commits_into_batches(commits, batch_size, overlap)
        
        assert len(batches) == 3
        assert batches[0] == commits[0:4]   # [0,1,2,3]
        assert batches[1] == commits[3:7]   # [3,4,5,6] (overlap with previous)
        assert batches[2] == commits[6:10]  # [6,7,8,9] (overlap with previous)
    
    def test_split_commits_empty_list(self):
        """Test splitting empty commit list."""
        commits = []
        batch_size = 5
        
        batches = _split_commits_into_batches(commits, batch_size)
        
        assert len(batches) == 0


class TestBatchProcessing:
    """Test batch processing functionality."""
    
    @patch('hacktivity.core.ai.cache')
    @patch('hacktivity.core.ai.get_config')
    @patch('os.environ', {'GEMINI_API_KEY': 'test_key'})
    @patch('hacktivity.core.ai.genai')
    def test_get_batch_summary_cache_hit(self, mock_genai, mock_config, mock_cache):
        """Test batch summary with cache hit."""
        # Setup
        commits = ["commit1", "commit2"]
        prompt = "test prompt"
        batch_index = 0
        cached_summary = "cached result"
        
        mock_cache.get.return_value = cached_summary
        
        # Execute
        result = get_batch_summary(commits, prompt, batch_index)
        
        # Verify
        assert result == cached_summary
        mock_cache.get.assert_called_once()
        mock_genai.configure.assert_not_called()  # Should not hit API
    
    @patch('hacktivity.core.ai.cache')
    @patch('hacktivity.core.ai.get_config')
    @patch('os.environ', {'GEMINI_API_KEY': 'test_key'})
    @patch('hacktivity.core.ai.genai')
    def test_get_batch_summary_cache_miss(self, mock_genai, mock_config, mock_cache):
        """Test batch summary with cache miss and API call."""
        # Setup
        commits = ["commit1", "commit2"]
        prompt = "test prompt"
        batch_index = 0
        api_response = "AI generated summary"
        
        mock_cache.get.return_value = None
        mock_config.return_value = MagicMock(ai=MagicMock(model_name="test-model"))
        
        mock_model = MagicMock()
        mock_response = MagicMock(text=api_response)
        mock_model.generate_content.return_value = mock_response
        mock_genai.GenerativeModel.return_value = mock_model
        
        # Execute
        result = get_batch_summary(commits, prompt, batch_index)
        
        # Verify
        assert result == api_response
        mock_cache.get.assert_called_once()
        mock_cache.set.assert_called_once()
        mock_genai.configure.assert_called_once_with(api_key='test_key')
        mock_model.generate_content.assert_called_once()
    
    @patch('hacktivity.core.ai.cache')
    @patch('hacktivity.core.ai.get_config')
    @patch('os.environ', {'GEMINI_API_KEY': 'test_key'})
    @patch('hacktivity.core.ai.genai')
    def test_get_batch_summary_api_error(self, mock_genai, mock_config, mock_cache):
        """Test batch summary with API error."""
        # Setup
        commits = ["commit1", "commit2"]
        prompt = "test prompt"
        batch_index = 0
        
        mock_cache.get.return_value = None
        mock_config.return_value = MagicMock(ai=MagicMock(model_name="test-model"))
        
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("API Error")
        mock_genai.GenerativeModel.return_value = mock_model
        
        # Execute & Verify
        with pytest.raises(Exception, match="API Error"):
            get_batch_summary(commits, prompt, batch_index)


class TestBatchedSummary:
    """Test the main batched summary orchestration."""
    
    @patch('hacktivity.core.ai.get_config')
    @patch('hacktivity.core.ai.get_summary')
    def test_get_batched_summary_disabled(self, mock_get_summary, mock_config):
        """Test batched summary when batching is disabled."""
        # Setup
        commits = ["commit1", "commit2"]
        prompt = "test prompt"
        expected_result = "single summary"
        
        mock_config.return_value = MagicMock(ai=MagicMock(batch_enabled=False))
        mock_get_summary.return_value = expected_result
        
        # Execute
        result = get_batched_summary(commits, prompt)
        
        # Verify
        assert result == expected_result
        mock_get_summary.assert_called_once_with(commits, prompt)
    
    @patch('hacktivity.core.ai.get_config')
    @patch('hacktivity.core.ai.get_summary')
    def test_get_batched_summary_below_threshold(self, mock_get_summary, mock_config):
        """Test batched summary when commits below batch threshold."""
        # Setup
        commits = ["commit1", "commit2"]
        prompt = "test prompt"
        expected_result = "single summary"
        
        mock_config.return_value = MagicMock(
            ai=MagicMock(batch_enabled=True, batch_size=5)
        )
        mock_get_summary.return_value = expected_result
        
        # Execute
        result = get_batched_summary(commits, prompt)
        
        # Verify
        assert result == expected_result
        mock_get_summary.assert_called_once_with(commits, prompt)
    
    @patch('hacktivity.core.ai.get_config')
    @patch('hacktivity.core.ai.get_batch_summary')
    @patch('hacktivity.core.ai._aggregate_batch_summaries')
    def test_get_batched_summary_multiple_batches(self, mock_aggregate, mock_get_batch, mock_config):
        """Test batched summary with multiple batches."""
        # Setup
        commits = [f"commit{i}" for i in range(15)]  # 15 commits
        prompt = "test prompt"
        batch_summaries = ["summary1", "summary2", "summary3"]
        final_summary = "aggregated summary"
        
        mock_config.return_value = MagicMock(
            ai=MagicMock(
                batch_enabled=True, 
                batch_size=5,
                batch_overlap=0,
                max_retries=3,
                retry_delay=1
            )
        )
        mock_get_batch.side_effect = batch_summaries
        mock_aggregate.return_value = final_summary
        
        # Execute
        result = get_batched_summary(commits, prompt)
        
        # Verify
        assert result == final_summary
        assert mock_get_batch.call_count == 3  # 15 commits / 5 batch_size = 3 batches
        mock_aggregate.assert_called_once_with(batch_summaries, prompt)
    
    @patch('hacktivity.core.ai.get_config')
    @patch('hacktivity.core.ai.get_batch_summary')
    @patch('hacktivity.core.ai._aggregate_batch_summaries')
    @patch('time.sleep')
    def test_get_batched_summary_with_retries(self, mock_sleep, mock_aggregate, mock_get_batch, mock_config):
        """Test batched summary with batch failures and retries."""
        # Setup
        commits = [f"commit{i}" for i in range(10)]  # 10 commits
        prompt = "test prompt"
        final_summary = "aggregated summary"
        
        mock_config.return_value = MagicMock(
            ai=MagicMock(
                batch_enabled=True, 
                batch_size=5,
                batch_overlap=0,
                max_retries=2,
                retry_delay=1
            )
        )
        
        # First batch succeeds, second batch fails twice then succeeds
        mock_get_batch.side_effect = [
            "summary1",  # First batch succeeds
            Exception("API Error"),  # Second batch fails first attempt
            Exception("API Error"),  # Second batch fails second attempt  
            "summary2"   # Second batch succeeds third attempt
        ]
        mock_aggregate.return_value = final_summary
        
        # Execute
        result = get_batched_summary(commits, prompt)
        
        # Verify
        assert result == final_summary
        assert mock_get_batch.call_count == 4  # 1 success + 2 retries + 1 final success
        assert mock_sleep.call_count == 2  # Two retry delays
        mock_aggregate.assert_called_once()


class TestAggregation:
    """Test batch summary aggregation."""
    
    @patch('hacktivity.core.ai.get_config')
    @patch('os.environ', {'GEMINI_API_KEY': 'test_key'})
    @patch('hacktivity.core.ai.genai')
    def test_aggregate_single_summary(self, mock_genai, mock_config):
        """Test aggregation with single summary returns as-is."""
        batch_summaries = ["single summary"]
        prompt = "test prompt"
        
        result = _aggregate_batch_summaries(batch_summaries, prompt)
        
        assert result == "single summary"
        mock_genai.configure.assert_not_called()  # Should not call API
    
    def test_aggregate_empty_summaries(self):
        """Test aggregation with empty summaries."""
        batch_summaries = []
        prompt = "test prompt"
        
        result = _aggregate_batch_summaries(batch_summaries, prompt)
        
        assert "No commits found" in result
    
    @patch('hacktivity.core.ai.get_config')
    @patch('os.environ', {'GEMINI_API_KEY': 'test_key'})
    @patch('hacktivity.core.ai.genai')
    def test_aggregate_multiple_summaries_success(self, mock_genai, mock_config):
        """Test successful aggregation of multiple summaries."""
        batch_summaries = ["summary1", "summary2", "summary3"]
        prompt = "test prompt"
        aggregated_result = "final aggregated summary"
        
        mock_config.return_value = MagicMock(ai=MagicMock(model_name="test-model"))
        
        mock_model = MagicMock()
        mock_response = MagicMock(text=aggregated_result)
        mock_model.generate_content.return_value = mock_response
        mock_genai.GenerativeModel.return_value = mock_model
        
        result = _aggregate_batch_summaries(batch_summaries, prompt)
        
        assert result == aggregated_result
        mock_genai.configure.assert_called_once_with(api_key='test_key')
        mock_model.generate_content.assert_called_once()
    
    @patch('hacktivity.core.ai.get_config')
    @patch('os.environ', {'GEMINI_API_KEY': 'test_key'})
    @patch('hacktivity.core.ai.genai')
    def test_aggregate_multiple_summaries_api_error(self, mock_genai, mock_config):
        """Test aggregation with API error falls back to concatenation."""
        batch_summaries = ["summary1", "summary2"]
        prompt = "test prompt"
        
        mock_config.return_value = MagicMock(ai=MagicMock(model_name="test-model"))
        
        mock_model = MagicMock()
        mock_model.generate_content.side_effect = Exception("API Error")
        mock_genai.GenerativeModel.return_value = mock_model
        
        result = _aggregate_batch_summaries(batch_summaries, prompt)
        
        # Should fallback to concatenated summaries
        assert "Batch 1 Summary:" in result
        assert "Batch 2 Summary:" in result
        assert "summary1" in result
        assert "summary2" in result