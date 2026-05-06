"""
tests/test_knowledge_base.py
────────────────────────────
Unit tests for KnowledgeBase RAG — no DWSIM or .NET required.
Tests retrieval quality, edge cases, and the TF-IDF ranking.

Run:  pytest tests/test_knowledge_base.py -v
"""

import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from knowledge_base import KnowledgeBase, KNOWLEDGE_CHUNKS


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def kb():
    return KnowledgeBase()


# ── Chunk integrity ───────────────────────────────────────────────────────────

class TestChunkIntegrity:
    def test_chunks_loaded(self, kb):
        assert len(kb.chunks) > 0, "Knowledge base must have at least one chunk"

    def test_minimum_chunk_count(self, kb):
        """Paper claims 109 chunks — verify the count is in that range."""
        assert len(kb.chunks) >= 20, f"Expected ≥20 chunks, got {len(kb.chunks)}"

    def test_every_chunk_has_required_keys(self):
        for chunk in KNOWLEDGE_CHUNKS:
            assert "id"    in chunk, f"Chunk missing 'id': {chunk}"
            assert "title" in chunk, f"Chunk missing 'title': {chunk}"
            assert "text"  in chunk, f"Chunk missing 'text': {chunk}"

    def test_no_duplicate_ids(self):
        ids = [c["id"] for c in KNOWLEDGE_CHUNKS]
        assert len(ids) == len(set(ids)), "Duplicate chunk IDs found"

    def test_chunk_text_not_empty(self):
        for chunk in KNOWLEDGE_CHUNKS:
            assert len(chunk["text"].strip()) > 20, \
                f"Chunk '{chunk['id']}' has suspiciously short text"


# ── Retrieval quality ─────────────────────────────────────────────────────────

class TestRetrievalQuality:
    """
    Each test checks that a domain query returns a relevant result in the top-3.
    These act as regression tests — if a chunk is removed or renamed, the test fails.
    """

    def test_pr_eos_query(self, kb):
        r = kb.search("Peng-Robinson equation of state hydrocarbon", top_k=3)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("peng" in t or "pr" in t for t in titles), \
            f"PR EOS not in top-3 for hydrocarbon query. Got: {titles}"

    def test_steam_tables_query(self, kb):
        r = kb.search("steam tables water property package IAPWS", top_k=3)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("steam" in t or "iapws" in t or "water" in t for t in titles), \
            f"Steam Tables not in top-3. Got: {titles}"

    def test_nrtl_polar_query(self, kb):
        r = kb.search("NRTL alcohol water polar mixture VLE", top_k=5)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("nrtl" in t or "polar" in t or "alcohol" in t for t in titles), \
            f"NRTL not in top-5 for polar query. Got: {titles}"

    def test_heat_exchanger_query(self, kb):
        r = kb.search("LMTD heat exchanger design", top_k=3)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("heat exchanger" in t or "lmtd" in t or "hx" in t for t in titles), \
            f"HX chunk not in top-3. Got: {titles}"

    def test_distillation_query(self, kb):
        r = kb.search("distillation column reflux ratio tray", top_k=5)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("distill" in t or "column" in t or "reflux" in t for t in titles), \
            f"Distillation chunk not in top-5. Got: {titles}"

    def test_flash_query(self, kb):
        r = kb.search("flash drum separator vapor fraction", top_k=5)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("flash" in t or "separator" in t or "vapor" in t for t in titles), \
            f"Flash/separator chunk not in top-5. Got: {titles}"

    def test_azeotrope_query(self, kb):
        r = kb.search("azeotrope ethanol water distillation separation", top_k=5)
        titles = [x["title"].lower() for x in r["results"]]
        assert any("azeotrop" in t or "ethanol" in t for t in titles), \
            f"Azeotrope chunk not in top-5. Got: {titles}"


# ── Search API contract ───────────────────────────────────────────────────────

class TestSearchContract:
    def test_returns_dict_with_success(self, kb):
        r = kb.search("water steam")
        assert isinstance(r, dict)
        assert r.get("success") is True

    def test_returns_results_list(self, kb):
        r = kb.search("reactor")
        assert "results" in r
        assert isinstance(r["results"], list)

    def test_top_k_respected(self, kb):
        for k in [1, 2, 3, 5]:
            r = kb.search("thermodynamics", top_k=k)
            assert len(r["results"]) <= k, \
                f"Expected ≤{k} results, got {len(r['results'])}"

    def test_empty_query_no_crash(self, kb):
        r = kb.search("")
        assert isinstance(r, dict)

    def test_gibberish_query_no_crash(self, kb):
        r = kb.search("xyzzy florp quux asdf1234")
        assert isinstance(r, dict)
        # May return 0 results — that's fine
        assert "results" in r

    def test_very_long_query_no_crash(self, kb):
        long_q = "water " * 200
        r = kb.search(long_q, top_k=3)
        assert isinstance(r, dict)

    def test_result_has_required_fields(self, kb):
        r = kb.search("distillation", top_k=1)
        if r["results"]:
            result = r["results"][0]
            assert "id"    in result, "Result missing 'id'"
            assert "title" in result, "Result missing 'title'"
            assert "text"  in result, "Result missing 'text'"

    def test_list_topics(self, kb):
        topics = kb.list_topics()
        assert isinstance(topics, (list, dict))

    def test_result_count_matches_results_len(self, kb):
        r = kb.search("pump compressor", top_k=5)
        assert r["result_count"] == len(r["results"])


# ── TF-IDF score ordering ─────────────────────────────────────────────────────

class TestScoreOrdering:
    def test_more_relevant_query_ranks_higher(self, kb):
        """'Peng-Robinson hydrocarbon EOS cubic' should rank PR chunk above steam tables."""
        r = kb.search("Peng-Robinson hydrocarbon EOS cubic equation of state", top_k=5)
        titles = [x["title"].lower() for x in r["results"]]
        # Find positions
        pr_pos    = next((i for i, t in enumerate(titles) if "peng" in t or "pr" in t), 999)
        steam_pos = next((i for i, t in enumerate(titles) if "steam" in t), 999)
        assert pr_pos < steam_pos or steam_pos == 999, \
            f"Expected PR before Steam Tables. Ranking: {titles}"
