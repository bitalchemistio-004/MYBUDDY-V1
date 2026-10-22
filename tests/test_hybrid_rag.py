import tempfile
import pytest
from pathlib import Path
from brain.obsidian_vault import Vault, DEFAULT_RATE

def test_hybrid_rag_graph_and_vector():
    with tempfile.TemporaryDirectory() as tmp_dir:
        vault = Vault(tmp_dir)
        vault.init()
        
        # 1. Create linked notes
        path_a = vault.upsert("Knowledge", "Python COM Automation", 
                             "Learn how to drive Windows apps using [[Excel Scripting]] and [[Word Automation]].")
        path_b = vault.upsert("Knowledge", "Excel Scripting", 
                             "Excel scripting allows automating spreadsheets via win32com.")
        path_c = vault.upsert("Knowledge", "Word Automation", 
                             "Word automation generates documents from templates.")
        
        # 2. Test graph construction
        index = {}
        for path in vault.notes():
            meta, body, _ = vault.read(path)
            index[path.stem.lower()] = {
                "path": path, "title": path.stem, "kind": path.parent.name,
                "meta": meta, "text": body, "links": [l.lower() for l in ["Excel Scripting", "Word Automation"]] if path == path_a else []
            }
            
        G = vault._build_graph(index)
        assert "python com automation" in G.nodes
        assert G.has_edge("python com automation", "excel scripting")
        
        # 3. Test graph score propagation
        seeds = {"python com automation": 1.0}
        graph_scores = vault._graph_scores(G, seeds)
        assert "excel scripting" in graph_scores
        assert graph_scores["excel scripting"] > 0.0
        
        # 4. Test vault retrieve with graph boosting
        results = vault.retrieve("Python COM Automation", token_budget=400)
        assert len(results) >= 1
        titles = [r["title"] for r in results]
        assert "Python COM Automation" in titles
