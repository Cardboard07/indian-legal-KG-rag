"""Multi-Node LLM Cluster Dispatcher and Load Balancer.
Dynamically discovers live worker nodes from Slurm registry files, performs health
checks, and balances inference requests across the cluster.
"""

import os
import glob
import json
import time
import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

try:
    import aiohttp
except ImportError:
    aiohttp = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ClusterDispatcher")


class LLMNode:
    """Represents an individual LLM worker node running on the cluster."""

    def __init__(self, node_info: Dict[str, Any]):
        self.job_id = node_info.get("job_id", "unknown")
        self.task_id = node_info.get("task_id", "0")
        self.hostname = node_info.get("hostname", "unknown")
        self.ip = node_info.get("ip", "127.0.0.1")
        self.port = int(node_info.get("port", 8080))
        self.url = node_info.get("url") or f"http://{self.ip}:{self.port}"
        self.gpu = node_info.get("gpu", "Unknown GPU")
        self.model = node_info.get("model", "Unknown Model")
        
        self.is_healthy: bool = False
        self.last_latency_ms: float = 0.0
        self.active_requests: int = 0
        self.total_completed: int = 0
        self.total_errors: int = 0

    @property
    def id(self) -> str:
        return f"{self.hostname}:{self.port} (Job {self.job_id}.{self.task_id})"

    def check_health_sync(self, timeout_sec: float = 3.0) -> bool:
        """Pings the node health using standard library urllib (zero dependencies)."""
        import urllib.request
        import urllib.error

        t0 = time.time()
        for endpoint in [f"{self.url}/health", f"{self.url}/v1/models"]:
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "ClusterDispatcher"})
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    if resp.status == 200:
                        self.is_healthy = True
                        self.last_latency_ms = (time.time() - t0) * 1000.0
                        return True
            except Exception:
                pass

        self.is_healthy = False
        return False

    async def check_health(self, session: Optional["aiohttp.ClientSession"] = None, timeout_sec: float = 3.0) -> bool:
        """Pings the node's health / models endpoint with aiohttp or urllib fallback."""
        if not session or not aiohttp:
            return await asyncio.to_thread(self.check_health_sync, timeout_sec)

        t0 = time.time()
        try:
            async with session.get(f"{self.url}/health", timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
                if resp.status == 200:
                    self.is_healthy = True
                    self.last_latency_ms = (time.time() - t0) * 1000.0
                    return True
        except Exception:
            pass

        try:
            async with session.get(f"{self.url}/v1/models", timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
                if resp.status == 200:
                    self.is_healthy = True
                    self.last_latency_ms = (time.time() - t0) * 1000.0
                    return True
        except Exception:
            pass

        self.is_healthy = False
        return False


class MultiNodeLLMDispatcher:
    """Orchestrates and load-balances inference across multiple Slurm GPU worker nodes."""

    def __init__(
        self,
        registry_dir: Optional[str] = None,
        explicit_urls: Optional[List[str]] = None,
        request_timeout: float = 120.0,
    ):
        username = os.environ.get("USER", "unknown")
        default_registry = f"/home/{username}/llm_cluster/registry"
        self.registry_dir = Path(registry_dir or os.environ.get("LLM_REGISTRY_DIR", default_registry))
        self.explicit_urls = explicit_urls or []
        self.request_timeout = request_timeout
        self.nodes: Dict[str, LLMNode] = {}
        self._round_robin_idx: int = 0
        self._lock = asyncio.Lock()

    def discover_nodes(self) -> List[LLMNode]:
        """Discovers nodes from registry JSON files and explicit URLs."""
        discovered: Dict[str, LLMNode] = {}

        # 1. Check explicit URLs
        for i, url in enumerate(self.explicit_urls):
            clean_url = url.rstrip("/")
            discovered[clean_url] = LLMNode({
                "job_id": "static",
                "task_id": str(i),
                "hostname": clean_url.replace("http://", "").split(":")[0],
                "port": clean_url.split(":")[-1] if ":" in clean_url else 8080,
                "url": clean_url,
                "gpu": "Pre-configured",
                "model": "Default",
            })

        # 2. Check registry directory
        if self.registry_dir.exists():
            json_files = glob.glob(str(self.registry_dir / "node_*.json"))
            for path in json_files:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        url = data.get("url") or f"http://{data.get('ip')}:{data.get('port')}"
                        # Preserve existing metrics if node already tracked
                        if url in self.nodes:
                            node = self.nodes[url]
                        else:
                            node = LLMNode(data)
                        discovered[url] = node
                except Exception as e:
                    logger.warning(f"Failed to parse registry file {path}: {e}")

        self.nodes = discovered
        return list(self.nodes.values())

    async def update_health(self) -> List[LLMNode]:
        """Refreshes discovery and verifies all nodes' health concurrently."""
        self.discover_nodes()
        if not self.nodes:
            return []

        if aiohttp:
            async with aiohttp.ClientSession() as session:
                tasks = [node.check_health(session) for node in self.nodes.values()]
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            tasks = [node.check_health(None) for node in self.nodes.values()]
            await asyncio.gather(*tasks, return_exceptions=True)

        healthy_nodes = [n for n in self.nodes.values() if n.is_healthy]
        return healthy_nodes

    def get_healthy_nodes(self) -> List[LLMNode]:
        return [n for n in self.nodes.values() if n.is_healthy]

    def _select_node(self) -> Optional[LLMNode]:
        """Selects the node with the fewest active requests (least-loaded), with round-robin tiebreaking."""
        healthy = self.get_healthy_nodes()
        if not healthy:
            return None

        # Sort primarily by active_requests, secondarily by last_latency
        healthy.sort(key=lambda n: (n.active_requests, n.last_latency_ms))
        return healthy[0]

    def _chat_completion_urllib(self, node: LLMNode, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous chat completion using standard library urllib."""
        import urllib.request
        import urllib.error

        endpoint = f"{node.url}/v1/chat/completions"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "ClusterDispatcher"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
        response_format: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """Sends an OpenAI-compatible /v1/chat/completions request to an available worker node."""
        payload: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format

        last_error = None

        for attempt in range(max_retries):
            node = self._select_node()
            if not node:
                # Refresh health in case nodes just came online
                await self.update_health()
                node = self._select_node()

            if not node:
                raise RuntimeError(
                    f"No healthy LLM worker nodes available in cluster! Total registered: {len(self.nodes)}."
                )

            node.active_requests += 1

            try:
                if aiohttp:
                    endpoint = f"{node.url}/v1/chat/completions"
                    timeout = aiohttp.ClientTimeout(total=self.request_timeout)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(endpoint, json=payload) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                node.active_requests = max(0, node.active_requests - 1)
                                node.total_completed += 1
                                return data
                            else:
                                err_text = await resp.text()
                                logger.warning(
                                    f"Worker {node.id} returned HTTP {resp.status} on attempt {attempt+1}: {err_text[:200]}"
                                )
                                node.total_errors += 1
                                last_error = RuntimeError(f"HTTP {resp.status}: {err_text}")
                else:
                    data = await asyncio.to_thread(self._chat_completion_urllib, node, payload)
                    node.active_requests = max(0, node.active_requests - 1)
                    node.total_completed += 1
                    return data
            except Exception as e:
                logger.warning(f"Worker {node.id} request failed on attempt {attempt+1}: {e}")
                node.is_healthy = False  # Mark node unhealthy on connection drops
                node.total_errors += 1
                last_error = e
            finally:
                node.active_requests = max(0, node.active_requests - 1)

            # Wait briefly before retrying next node
            await asyncio.sleep(0.5)

        raise RuntimeError(f"Chat completion failed after {max_retries} attempts across cluster: {last_error}")

    async def batch_chat_completions(
        self,
        batch_inputs: List[List[Dict[str, str]]],
        temperature: float = 0.1,
        max_tokens: int = 2048,
        response_format: Optional[Dict[str, Any]] = None,
        max_concurrency: Optional[int] = None,
    ) -> List[Optional[Dict[str, Any]]]:
        """Processes a list of chat completion prompts across all cluster nodes concurrently."""
        healthy = await self.update_health()
        if not healthy:
            raise RuntimeError("Cannot start batch: 0 healthy nodes in cluster.")

        # Default concurrency: 2 requests per active GPU node
        concurrency = max_concurrency or (len(healthy) * 2)
        semaphore = asyncio.Semaphore(concurrency)

        async def _worker(item_idx: int, messages: List[Dict[str, str]]):
            async with semaphore:
                try:
                    res = await self.chat_completion(
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                    )
                    return item_idx, res, None
                except Exception as ex:
                    return item_idx, None, str(ex)

        tasks = [_worker(i, msgs) for i, msgs in enumerate(batch_inputs)]
        results = [None] * len(batch_inputs)

        for coro in asyncio.as_completed(tasks):
            idx, res, err = await coro
            if err:
                logger.error(f"Batch item {idx} failed: {err}")
            results[idx] = res

        return results
