"""Utilities for measuring execution time and memory usage in large-scale tests."""
import gc
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Any, Optional, List, Callable

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


@dataclass
class PerformanceMetrics:
    """Container for performance measurement results."""
    duration_seconds: float
    peak_memory_mb: float
    average_memory_mb: float
    initial_memory_mb: float
    final_memory_mb: float
    cpu_percent: float
    gc_collections: Dict[int, int]
    custom_metrics: Dict[str, Any]


class PerformanceProfiler:
    """
    A context manager to profile execution time, memory usage, and custom metrics.
    
    Provides detailed performance measurement for large-scale integration tests
    including memory profiling, CPU usage, and garbage collection statistics.
    """
    
    def __init__(self, name: str = "test", collect_gc_stats: bool = True):
        """
        Initialize the performance profiler.
        
        Args:
            name: Name for this profiling session
            collect_gc_stats: Whether to collect garbage collection statistics
        """
        self.name = name
        self.collect_gc_stats = collect_gc_stats
        self.custom_metrics = {}
        
        # Performance metrics
        self.duration_seconds = 0.0
        self.peak_memory_mb = 0.0
        self.average_memory_mb = 0.0
        self.initial_memory_mb = 0.0
        self.final_memory_mb = 0.0
        self.cpu_percent = 0.0
        self.gc_collections = {}
        
        # Internal tracking
        self._start_time = 0.0
        self._initial_gc_counts = {}
        self._memory_samples = []
        self._monitoring_thread = None
        self._stop_monitoring = False
        
        if HAS_PSUTIL:
            self.process = psutil.Process()
        else:
            self.process = None
            
    def add_custom_metric(self, name: str, value: Any):
        """Add a custom metric to track."""
        self.custom_metrics[name] = value
    
    def _get_memory_usage_mb(self) -> float:
        """Get current memory usage in MB."""
        if self.process:
            return self.process.memory_info().rss / (1024 * 1024)
        return 0.0
    
    def _get_cpu_percent(self) -> float:
        """Get CPU usage percentage."""
        if self.process:
            return self.process.cpu_percent()
        return 0.0
    
    def _memory_monitor(self):
        """Background thread to monitor memory usage."""
        while not self._stop_monitoring:
            try:
                memory_mb = self._get_memory_usage_mb()
                self._memory_samples.append(memory_mb)
                self.peak_memory_mb = max(self.peak_memory_mb, memory_mb)
                time.sleep(0.1)  # Sample every 100ms
            except Exception:
                # Ignore errors in monitoring thread
                pass
    
    def __enter__(self):
        """Start profiling."""
        # Collect garbage before starting
        if self.collect_gc_stats:
            gc.collect()
            self._initial_gc_counts = {i: gc.get_count()[i] for i in range(3)}
        
        # Record initial state
        self.initial_memory_mb = self._get_memory_usage_mb()
        self.peak_memory_mb = self.initial_memory_mb
        self._memory_samples = [self.initial_memory_mb]
        
        # Start CPU monitoring
        if self.process:
            self.process.cpu_percent()  # Initialize CPU monitoring
        
        # Start memory monitoring thread
        self._stop_monitoring = False
        self._monitoring_thread = threading.Thread(target=self._memory_monitor, daemon=True)
        self._monitoring_thread.start()
        
        # Record start time
        self._start_time = time.perf_counter()
        
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop profiling and calculate metrics."""
        # Stop timing
        self.duration_seconds = time.perf_counter() - self._start_time
        
        # Stop memory monitoring
        self._stop_monitoring = True
        if self._monitoring_thread:
            self._monitoring_thread.join(timeout=1.0)
        
        # Record final state
        self.final_memory_mb = self._get_memory_usage_mb()
        self.cpu_percent = self._get_cpu_percent()
        
        # Calculate memory statistics
        if self._memory_samples:
            self.average_memory_mb = sum(self._memory_samples) / len(self._memory_samples)
        
        # Collect final garbage collection stats
        if self.collect_gc_stats:
            final_gc_counts = {i: gc.get_count()[i] for i in range(3)}
            self.gc_collections = {
                i: final_gc_counts[i] - self._initial_gc_counts[i]
                for i in range(3)
            }
    
    def get_metrics(self) -> PerformanceMetrics:
        """Get performance metrics as a structured object."""
        return PerformanceMetrics(
            duration_seconds=self.duration_seconds,
            peak_memory_mb=self.peak_memory_mb,
            average_memory_mb=self.average_memory_mb,
            initial_memory_mb=self.initial_memory_mb,
            final_memory_mb=self.final_memory_mb,
            cpu_percent=self.cpu_percent,
            gc_collections=self.gc_collections,
            custom_metrics=self.custom_metrics.copy()
        )
    
    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of performance metrics."""
        return {
            'name': self.name,
            'duration_seconds': round(self.duration_seconds, 3),
            'peak_memory_mb': round(self.peak_memory_mb, 2),
            'average_memory_mb': round(self.average_memory_mb, 2),
            'memory_delta_mb': round(self.final_memory_mb - self.initial_memory_mb, 2),
            'cpu_percent': round(self.cpu_percent, 1),
            'gc_collections': self.gc_collections,
            'custom_metrics': self.custom_metrics,
            'psutil_available': HAS_PSUTIL
        }


class PerformanceBenchmark:
    """
    Benchmark utilities for establishing performance baselines and detecting regressions.
    """
    
    def __init__(self):
        self.benchmarks = {}
    
    def add_benchmark(self, name: str, target_duration: float, target_memory_mb: float,
                     target_throughput: float = None, tolerance: float = 0.2):
        """
        Add a performance benchmark.
        
        Args:
            name: Benchmark name
            target_duration: Target execution time in seconds
            target_memory_mb: Target peak memory in MB
            target_throughput: Target throughput (items/second)
            tolerance: Acceptable deviation (0.2 = 20%)
        """
        self.benchmarks[name] = {
            'target_duration': target_duration,
            'target_memory_mb': target_memory_mb,
            'target_throughput': target_throughput,
            'tolerance': tolerance
        }
    
    def check_benchmark(self, name: str, metrics: PerformanceMetrics, 
                       throughput: float = None) -> Dict[str, Any]:
        """
        Check if metrics meet benchmark requirements.
        
        Args:
            name: Benchmark name
            metrics: Performance metrics to check
            throughput: Actual throughput (items/second)
            
        Returns:
            Dictionary with benchmark results
        """
        if name not in self.benchmarks:
            return {'error': f'Benchmark {name} not found'}
        
        benchmark = self.benchmarks[name]
        tolerance = benchmark['tolerance']
        results = {'benchmark': name, 'passed': True, 'details': {}}
        
        # Check duration
        max_duration = benchmark['target_duration'] * (1 + tolerance)
        duration_passed = metrics.duration_seconds <= max_duration
        results['details']['duration'] = {
            'actual': metrics.duration_seconds,
            'target': benchmark['target_duration'],
            'max_allowed': max_duration,
            'passed': duration_passed
        }
        if not duration_passed:
            results['passed'] = False
        
        # Check memory
        max_memory = benchmark['target_memory_mb'] * (1 + tolerance)
        memory_passed = metrics.peak_memory_mb <= max_memory
        results['details']['memory'] = {
            'actual': metrics.peak_memory_mb,
            'target': benchmark['target_memory_mb'],
            'max_allowed': max_memory,
            'passed': memory_passed
        }
        if not memory_passed:
            results['passed'] = False
        
        # Check throughput if provided
        if benchmark['target_throughput'] and throughput is not None:
            min_throughput = benchmark['target_throughput'] * (1 - tolerance)
            throughput_passed = throughput >= min_throughput
            results['details']['throughput'] = {
                'actual': throughput,
                'target': benchmark['target_throughput'],
                'min_required': min_throughput,
                'passed': throughput_passed
            }
            if not throughput_passed:
                results['passed'] = False
        
        return results


@contextmanager
def measure_performance(name: str = "operation", 
                       collect_gc_stats: bool = True):
    """
    Context manager for quick performance measurement.
    
    Args:
        name: Name for the measurement
        collect_gc_stats: Whether to collect garbage collection statistics
        
    Yields:
        PerformanceProfiler instance
    """
    profiler = PerformanceProfiler(name, collect_gc_stats)
    with profiler:
        yield profiler


def create_standard_benchmarks() -> PerformanceBenchmark:
    """Create standard benchmarks for large-scale integration tests."""
    benchmark = PerformanceBenchmark()
    
    # Large-scale processing (10,000 commits)
    benchmark.add_benchmark(
        'large_scale_10k',
        target_duration=30.0,    # 30 seconds max
        target_memory_mb=500.0,  # 500MB max
        target_throughput=100.0, # 100 commits/second min
        tolerance=0.3            # 30% tolerance
    )
    
    # Medium-scale processing (1,000 commits)
    benchmark.add_benchmark(
        'medium_scale_1k',
        target_duration=5.0,     # 5 seconds max
        target_memory_mb=100.0,  # 100MB max
        target_throughput=200.0, # 200 commits/second min
        tolerance=0.2            # 20% tolerance
    )
    
    # Small-scale processing (100 commits)
    benchmark.add_benchmark(
        'small_scale_100',
        target_duration=1.0,     # 1 second max
        target_memory_mb=50.0,   # 50MB max
        target_throughput=500.0, # 500 commits/second min
        tolerance=0.2            # 20% tolerance
    )
    
    # Resume operation overhead
    benchmark.add_benchmark(
        'resume_overhead',
        target_duration=2.0,     # 2 seconds max additional overhead
        target_memory_mb=50.0,   # 50MB max additional memory
        tolerance=0.5            # 50% tolerance (resume can be variable)
    )
    
    return benchmark


def assert_performance_requirements(profiler: PerformanceProfiler, 
                                  max_duration: float = None,
                                  max_memory_mb: float = None,
                                  min_throughput: float = None,
                                  item_count: int = None):
    """
    Assert that performance requirements are met.
    
    Args:
        profiler: PerformanceProfiler instance with collected metrics
        max_duration: Maximum allowed duration in seconds
        max_memory_mb: Maximum allowed peak memory in MB
        min_throughput: Minimum required throughput (items/second)
        item_count: Number of items processed (for throughput calculation)
    """
    metrics = profiler.get_metrics()
    
    if max_duration is not None:
        assert metrics.duration_seconds <= max_duration, \
            f"Duration {metrics.duration_seconds:.2f}s exceeds limit of {max_duration}s"
    
    if max_memory_mb is not None:
        assert metrics.peak_memory_mb <= max_memory_mb, \
            f"Peak memory {metrics.peak_memory_mb:.2f}MB exceeds limit of {max_memory_mb}MB"
    
    if min_throughput is not None and item_count is not None:
        actual_throughput = item_count / metrics.duration_seconds
        assert actual_throughput >= min_throughput, \
            f"Throughput {actual_throughput:.2f} items/sec below minimum of {min_throughput}"


class ResourceMonitor:
    """Monitor system resources during test execution."""
    
    def __init__(self, sample_interval: float = 1.0):
        """
        Initialize resource monitor.
        
        Args:
            sample_interval: Interval between samples in seconds
        """
        self.sample_interval = sample_interval
        self.samples = []
        self._monitoring = False
        self._monitor_thread = None
        
        if HAS_PSUTIL:
            self.process = psutil.Process()
        else:
            self.process = None
    
    def start(self):
        """Start monitoring resources."""
        if not HAS_PSUTIL:
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
    
    def stop(self):
        """Stop monitoring resources."""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
    
    def _monitor_loop(self):
        """Background monitoring loop."""
        while self._monitoring:
            try:
                sample = {
                    'timestamp': time.time(),
                    'memory_mb': self.process.memory_info().rss / (1024 * 1024),
                    'cpu_percent': self.process.cpu_percent(),
                    'num_threads': self.process.num_threads(),
                }
                self.samples.append(sample)
                time.sleep(self.sample_interval)
            except Exception:
                # Ignore monitoring errors
                pass
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of resource usage."""
        if not self.samples:
            return {'error': 'No samples collected'}
        
        memory_values = [s['memory_mb'] for s in self.samples]
        cpu_values = [s['cpu_percent'] for s in self.samples]
        thread_values = [s['num_threads'] for s in self.samples]
        
        return {
            'duration_seconds': self.samples[-1]['timestamp'] - self.samples[0]['timestamp'],
            'sample_count': len(self.samples),
            'memory': {
                'peak_mb': max(memory_values),
                'average_mb': sum(memory_values) / len(memory_values),
                'min_mb': min(memory_values)
            },
            'cpu': {
                'peak_percent': max(cpu_values),
                'average_percent': sum(cpu_values) / len(cpu_values),
                'min_percent': min(cpu_values)
            },
            'threads': {
                'peak_count': max(thread_values),
                'average_count': sum(thread_values) / len(thread_values),
                'min_count': min(thread_values)
            }
        }