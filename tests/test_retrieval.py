from folio.retrieval import confidence, detect_scope, is_extraction, is_whole_document, rrf, select_passages
from folio.types import Doc


def _ids(kb, *prefixes):
    return [d.id for d in kb.docs.values() if d.name.startswith(prefixes)]


def test_rrf_rewards_agreement_between_rankings():
    fused = rrf([[1, 2, 3], [3, 1, 4]])
    order = [item for item, _ in fused]
    assert order[0] == 1  # ranks 1 and 2
    assert order[1] == 3  # ranks 3 and 1
    assert set(order) == {1, 2, 3, 4}
    assert abs(dict(fused)[1] - (1 / 61 + 1 / 62)) < 1e-12


def test_rrf_ties_are_deterministic():
    assert [i for i, _ in rrf([[5], [7]])] == [5, 7]


def test_scope_detects_names_coinages_and_cued_keywords(kb):
    docs = list(kb.docs.values())
    cv, paper, leak = _ids(kb, "cv"), _ids(kb, "Orbital"), _ids(kb, "Finding")
    assert detect_scope("summarize the CV", docs) == cv
    assert detect_scope("check cvJaneDoe.pdf", docs) == cv
    assert detect_scope("what's on the resume?", docs) == cv
    assert detect_scope("How well does OrbitalNet detect early crop disease?", docs) == paper
    assert detect_scope("What does the leaks paper propose?", docs) == leak


def test_scope_ignores_ordinary_questions(kb):
    docs = list(kb.docs.values())
    assert detect_scope("What accuracy was reported?", docs) == []
    assert detect_scope("Were the meters accurate?", docs) == []  # weak word, no document cue
    assert detect_scope("anything", []) == []


def test_weak_words_shared_between_names_dont_scope():
    docs = [Doc("a", "Theft report 2024.pdf", "pdf"), Doc("b", "Theft report 2025.pdf", "pdf")]
    assert detect_scope("summarize the theft report", docs) == []


def test_intents():
    assert is_whole_document("Summarise cvJaneDoe.pdf") and is_whole_document("check the CV")
    assert is_whole_document("What does the leak paper propose?")
    assert not is_whole_document("What accuracy did it reach?")
    assert is_extraction("Put every reported metric in a table") and is_extraction("list all deadlines")
    assert not is_extraction("What is the deadline?")


def test_search_scoped_to_a_document(kb):
    cv = _ids(kb, "cv")
    sel = select_passages(kb, "deep learning skills", "deep learning skills", k=4, scope=cv)
    assert sel.passages and all(p.chunk.doc == "cvJaneDoe.pdf" for p in sel.passages)
    assert sel.complete and confidence(sel)["level"] == "High"


def test_whole_document_spread_covers_start_middle_end(kb):
    paper = _ids(kb, "Orbital")
    sel = select_passages(kb, "summarize the paper", "summarize OrbitalNet", k=4, scope=paper)
    assert sel.mode == "whole"
    ordinals = [p.chunk.ordinal for p in sel.passages]
    assert ordinals == sorted(ordinals), "reading order"
    total = len(kb.docs[paper[0]].chunks)
    assert ordinals[0] == 0 and ordinals[-1] == total - 1


def test_extraction_prefers_figure_dense_passages(kb):
    sel = select_passages(kb, "every reported metric", "Put every reported metric in a table", k=4)
    assert sel.mode == "extract"
    assert any("90.20%" in p.chunk.text for p in sel.passages)


def test_keyword_only_hit_is_found(kb):
    sel = select_passages(kb, "LeafFormer", "LeafFormer", k=3)
    assert any("LeafFormer" in p.chunk.text for p in sel.passages)


def test_remove_and_persist(kb, tmp_path):
    from folio.index import KnowledgeBase
    from tests.conftest import FakeEmbedder

    cv = _ids(kb, "cv")[0]
    assert kb.remove(cv) and not kb.remove(cv)
    assert all(c.doc_id != cv for c in kb.chunks)
    kb.save(tmp_path)
    restored = KnowledgeBase(embedder=FakeEmbedder())
    assert restored.load(tmp_path) is False  # same model: index reused, no re-embedding
    assert [c.id for c in restored.chunks] == [c.id for c in kb.chunks]
    assert (restored.vectors == kb.vectors).all()
