"""Deterministic NetworkX citation graph construction and bounded metrics."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

import networkx as nx  # type: ignore[import-untyped]

from scholartrace.citations.models import (
    CitationEdge,
    CitationGraphArtifact,
    CitationNode,
    CitationNodeMetric,
    CitationPaperLifecycle,
    CitationTimelineCandidate,
    LifecycleStage,
    citation_graph_sha256,
)
from scholartrace.contracts import Paper


class CitationGraphBuilder:
    def build(
        self,
        *,
        papers: list[Paper],
        edges: list[CitationEdge],
        seed_paper_ids: list[str],
        lifecycle_records: list[CitationPaperLifecycle],
        generated_at: datetime,
        warnings: list[str] | None = None,
    ) -> CitationGraphArtifact:
        paper_by_id = {paper.canonical_paper_id: paper for paper in papers}
        if len(paper_by_id) != len(papers):
            raise ValueError("citation graph paper IDs must be unique")
        lifecycle_by_id = {
            record.canonical_paper_id: record for record in lifecycle_records
        }
        if len(lifecycle_by_id) != len(lifecycle_records):
            raise ValueError("citation lifecycle paper IDs must be unique")
        node_ids = set(paper_by_id) | {
            endpoint
            for edge in edges
            for endpoint in (edge.citing_paper_id, edge.cited_paper_id)
        }
        if not node_ids:
            raise ValueError("citation graph requires at least one node")
        seed_set = set(seed_paper_ids)
        nodes = [
            self._node(
                paper_id=paper_id,
                paper=paper_by_id.get(paper_id),
                lifecycle=lifecycle_by_id.get(paper_id),
                seed=paper_id in seed_set,
            )
            for paper_id in sorted(node_ids)
        ]

        graph: Any = nx.DiGraph()
        graph.add_nodes_from(node.paper_id for node in nodes)
        graph.add_edges_from(
            (edge.citing_paper_id, edge.cited_paper_id)
            for edge in sorted(edges, key=lambda item: item.edge_id)
        )
        components = sorted(
            (sorted(str(item) for item in component)
             for component in nx.weakly_connected_components(graph)),
            key=lambda item: item[0],
        )
        communities = self._communities(graph)
        component_by_node = self._membership(components, "component")
        community_by_node = self._membership(communities, "community")
        pagerank = self._bounded_pagerank(graph, sorted(node_ids))
        metrics = [
            CitationNodeMetric(
                paper_id=paper_id,
                in_degree=int(graph.in_degree(paper_id)),
                out_degree=int(graph.out_degree(paper_id)),
                pagerank=round(pagerank[paper_id], 12),
                component_id=component_by_node[paper_id],
                community_id=community_by_node[paper_id],
            )
            for paper_id in sorted(node_ids)
        ]
        timeline = self._timeline(edges=edges, paper_by_id=paper_by_id)
        sorted_warnings = sorted(set(warnings or []))
        graph_digest = hashlib.sha256(
            "\0".join(
                [
                    *(node.paper_id for node in nodes),
                    *(edge.edge_id for edge in sorted(edges, key=lambda item: item.edge_id)),
                ]
            ).encode("utf-8")
        ).hexdigest()
        draft = CitationGraphArtifact.model_construct(
            schema_version="1.0",
            graph_id=f"citation-graph:m4:{graph_digest[:24]}",
            nodes=nodes,
            edges=sorted(edges, key=lambda item: item.edge_id),
            metrics=metrics,
            components=components,
            communities=communities,
            timeline_candidates=timeline,
            partial=bool(sorted_warnings),
            warnings=sorted_warnings,
            generated_at=generated_at,
            content_sha256="0" * 64,
        )
        return CitationGraphArtifact.model_validate(
            {
                **draft.model_dump(mode="json", exclude={"content_sha256"}),
                "content_sha256": citation_graph_sha256(draft),
            }
        )

    @staticmethod
    def _node(
        *,
        paper_id: str,
        paper: Paper | None,
        lifecycle: CitationPaperLifecycle | None,
        seed: bool,
    ) -> CitationNode:
        stage: LifecycleStage | Literal["external_metadata_missing"]
        if lifecycle is not None:
            stage = lifecycle.stage
            ready = stage == "analyzed"
        elif paper is not None and seed:
            stage = "analyzed"
            ready = True
        else:
            stage = "external_metadata_missing"
            ready = False
        return CitationNode(
            paper_id=paper_id,
            title=paper.title if paper is not None else paper_id,
            publication_year=paper.publication_year if paper is not None else None,
            seed=seed,
            lifecycle_stage=stage,
            analysis_ready=ready,
        )

    @staticmethod
    def _communities(graph: Any) -> list[list[str]]:
        node_ids = sorted(str(node) for node in graph.nodes)
        if graph.number_of_edges() == 0:
            return [[node] for node in node_ids]
        raw = nx.algorithms.community.greedy_modularity_communities(
            graph.to_undirected()
        )
        communities = [sorted(str(item) for item in community) for community in raw]
        return sorted(communities, key=lambda item: item[0])

    @staticmethod
    def _membership(groups: list[list[str]], prefix: str) -> dict[str, str]:
        return {
            node: f"{prefix}:m4:{index}"
            for index, group in enumerate(groups, start=1)
            for node in group
        }

    @staticmethod
    def _bounded_pagerank(
        graph: Any,
        node_ids: list[str],
        *,
        damping: float = 0.85,
        tolerance: float = 1e-12,
        max_iterations: int = 100,
    ) -> dict[str, float]:
        count = len(node_ids)
        ranks = {node: 1.0 / count for node in node_ids}
        for _ in range(max_iterations):
            dangling = sum(ranks[node] for node in node_ids if graph.out_degree(node) == 0)
            next_ranks = {
                node: (1.0 - damping) / count + damping * dangling / count
                for node in node_ids
            }
            for source in node_ids:
                successors = sorted(str(item) for item in graph.successors(source))
                if not successors:
                    continue
                share = damping * ranks[source] / len(successors)
                for target in successors:
                    next_ranks[target] += share
            delta = sum(abs(next_ranks[node] - ranks[node]) for node in node_ids)
            ranks = next_ranks
            if delta <= tolerance:
                break
        total = sum(ranks.values())
        return {node: ranks[node] / total for node in node_ids}

    @staticmethod
    def _timeline(
        *,
        edges: list[CitationEdge],
        paper_by_id: dict[str, Paper],
    ) -> list[CitationTimelineCandidate]:
        candidates: list[CitationTimelineCandidate] = []
        for edge in sorted(edges, key=lambda item: item.edge_id):
            citing = paper_by_id.get(edge.citing_paper_id)
            cited = paper_by_id.get(edge.cited_paper_id)
            if citing is None or cited is None:
                continue
            if cited.publication_year > citing.publication_year:
                continue
            candidates.append(
                CitationTimelineCandidate(
                    earlier_paper_id=cited.canonical_paper_id,
                    later_paper_id=citing.canonical_paper_id,
                    year_gap=citing.publication_year - cited.publication_year,
                    citation_edge_id=edge.edge_id,
                )
            )
        return candidates
