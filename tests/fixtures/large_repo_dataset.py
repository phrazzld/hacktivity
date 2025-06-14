"""Generates large, realistic test datasets for repositories and commits."""
import uuid
import random
import string
from datetime import datetime, timedelta
from typing import Dict, List, Any


def generate_commit(commit_date: datetime, author_login: str = "test-author") -> Dict[str, Any]:
    """Generates a single mock commit dictionary matching GitHub API format."""
    sha = ''.join(random.choices(string.hexdigits.lower(), k=40))
    
    return {
        'sha': sha,
        'commit': {
            'message': f'feat: large scale commit {sha[:8]}',
            'author': {
                'name': 'Test Author',
                'email': f'{author_login}@example.com',
                'date': commit_date.isoformat() + 'Z'
            },
            'committer': {
                'name': 'Test Author',
                'email': f'{author_login}@example.com',
                'date': commit_date.isoformat() + 'Z'
            }
        },
        'author': {
            'login': author_login,
            'id': hash(author_login) % 1000000
        },
        'author_name': 'Test Author',
        'author_email': f'{author_login}@example.com',
        'author_login': author_login,
        'author_id': hash(author_login) % 1000000,
        'commit_date': commit_date.isoformat() + 'Z',
        'committer_name': 'Test Author',
        'committer_email': f'{author_login}@example.com',
        'committer_date': commit_date.isoformat() + 'Z',
        'url': f'https://api.github.com/repos/test-org/repo/commits/{sha}',
        'html_url': f'https://github.com/test-org/repo/commit/{sha}'
    }


def generate_repo_data(full_name: str, last_updated_date: datetime) -> Dict[str, Any]:
    """Generates a mock repository dictionary matching GitHub API format."""
    owner_name, repo_name = full_name.split('/')
    
    return {
        'full_name': full_name,
        'name': repo_name,
        'owner': {
            'login': owner_name,
            'id': hash(owner_name) % 1000000
        },
        'private': False,
        'language': 'Python',
        'created_at': '2020-01-01T00:00:00Z',
        'updated_at': last_updated_date.isoformat() + 'Z',
        'archived': False,
        'fork': False,
        'default_branch': 'main',
        'size': random.randint(100, 10000),
        'stargazers_count': random.randint(0, 500),
        'forks_count': random.randint(0, 50)
    }


def create_large_dataset(num_repos: int, commits_per_repo: int, since_str: str = "2024-01-01") -> Dict[str, List[Dict[str, Any]]]:
    """
    Creates a large dataset of repositories and their commits.

    Args:
        num_repos: Number of repositories to generate
        commits_per_repo: Number of commits per repository
        since_str: Start date in YYYY-MM-DD format

    Returns:
        Dictionary mapping 'repo_full_name' to a list of its commit dictionaries.
    """
    since_date = datetime.strptime(since_str, '%Y-%m-%d')
    commit_map = {}
    
    for i in range(num_repos):
        repo_name = f"test-org/repo-{i:03d}"
        commits = []
        
        for j in range(commits_per_repo):
            # Distribute commits over time to test date chunking
            commit_date = since_date + timedelta(days=j % 365)  # Wrap around after a year
            commits.append(generate_commit(commit_date))
        
        # Sort commits by date (newest first, as GitHub API returns them)
        commits.sort(key=lambda c: c['commit_date'], reverse=True)
        commit_map[repo_name] = commits
    
    return commit_map


def create_large_single_repo_dataset(repo_name: str, num_commits: int, since_str: str = "2024-01-01") -> Dict[str, List[Dict[str, Any]]]:
    """
    Creates a single repository with a large number of commits.
    
    Args:
        repo_name: Repository name (e.g., "test-org/huge-repo")
        num_commits: Number of commits to generate
        since_str: Start date in YYYY-MM-DD format
        
    Returns:
        Dictionary with single repository mapped to large commit list
    """
    since_date = datetime.strptime(since_str, '%Y-%m-%d')
    commits = []
    
    for i in range(num_commits):
        # Distribute commits over 2 years to test chunking
        commit_date = since_date + timedelta(days=i % 730)
        commits.append(generate_commit(commit_date))
    
    # Sort commits by date (newest first)
    commits.sort(key=lambda c: c['commit_date'], reverse=True)
    
    return {repo_name: commits}


def create_multi_author_dataset(num_repos: int, commits_per_repo: int, authors: List[str], since_str: str = "2024-01-01") -> Dict[str, List[Dict[str, Any]]]:
    """
    Creates a dataset with multiple authors for testing author filtering.
    
    Args:
        num_repos: Number of repositories to generate
        commits_per_repo: Number of commits per repository
        authors: List of author usernames
        since_str: Start date in YYYY-MM-DD format
        
    Returns:
        Dictionary mapping repository names to commit lists with varied authors
    """
    since_date = datetime.strptime(since_str, '%Y-%m-%d')
    commit_map = {}
    
    for i in range(num_repos):
        repo_name = f"test-org/multi-author-repo-{i:03d}"
        commits = []
        
        for j in range(commits_per_repo):
            # Randomly assign commits to different authors
            author = random.choice(authors)
            commit_date = since_date + timedelta(days=j % 365)
            commits.append(generate_commit(commit_date, author))
        
        # Sort commits by date (newest first)
        commits.sort(key=lambda c: c['commit_date'], reverse=True)
        commit_map[repo_name] = commits
    
    return commit_map


def create_uneven_workload_dataset() -> Dict[str, List[Dict[str, Any]]]:
    """
    Creates a dataset with uneven workloads to test parallel processing edge cases.
    
    Returns:
        Dictionary with repositories having vastly different commit counts
    """
    workloads = [
        ("test-org/tiny-repo", 5),
        ("test-org/small-repo", 50),
        ("test-org/medium-repo", 500),
        ("test-org/large-repo", 2000),
        ("test-org/huge-repo", 10000)
    ]
    
    commit_map = {}
    since_date = datetime(2024, 1, 1)
    
    for repo_name, commit_count in workloads:
        commits = []
        for i in range(commit_count):
            commit_date = since_date + timedelta(days=i % 365)
            commits.append(generate_commit(commit_date))
        
        # Sort commits by date (newest first)
        commits.sort(key=lambda c: c['commit_date'], reverse=True)
        commit_map[repo_name] = commits
    
    return commit_map


def calculate_expected_totals(commit_map: Dict[str, List[Dict[str, Any]]], author_filter: str = None) -> Dict[str, int]:
    """
    Calculate expected totals for verification in tests.
    
    Args:
        commit_map: Repository to commits mapping
        author_filter: Optional author to filter by
        
    Returns:
        Dictionary with expected counts
    """
    total_commits = 0
    total_repos = len(commit_map)
    repos_with_commits = 0
    
    for repo_name, commits in commit_map.items():
        if author_filter:
            filtered_commits = [c for c in commits if c.get('author_login') == author_filter]
            repo_commit_count = len(filtered_commits)
        else:
            repo_commit_count = len(commits)
        
        total_commits += repo_commit_count
        if repo_commit_count > 0:
            repos_with_commits += 1
    
    return {
        'total_commits': total_commits,
        'total_repositories': total_repos,
        'repositories_with_commits': repos_with_commits,
        'average_commits_per_repo': total_commits / total_repos if total_repos > 0 else 0
    }